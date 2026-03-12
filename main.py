import argparse
import re
import shutil
import tarfile
from pathlib import Path

import numpy as np
import yaml
import xarray as xr
import rioxarray  # noqa: F401
from rasterio.transform import from_bounds

import rasterio
from rasterio.warp import calculate_default_transform, reproject
from rasterio.enums import Resampling


def load_cfg(p: Path) -> dict:
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve_path(p: str, base_dir: Path) -> Path:
    pp = Path(p)
    if pp.is_absolute():
        return pp
    return (base_dir / pp).resolve()


def pick_dim_name(names, candidates):
    for c in candidates:
        if c in names:
            return c
    return None


def infer_transform_from_1d_coords(x, y):
    x = np.asarray(x)
    y = np.asarray(y)
    if x.ndim != 1 or y.ndim != 1 or x.size < 2 or y.size < 2:
        return None

    xmin, xmax = float(np.min(x)), float(np.max(x))
    ymin, ymax = float(np.min(y)), float(np.max(y))
    width = x.size
    height = y.size

    dx = (xmax - xmin) / (width - 1)
    dy = (ymax - ymin) / (height - 1)

    left = xmin - dx / 2.0
    right = xmax + dx / 2.0
    bottom = ymin - dy / 2.0
    top = ymax + dy / 2.0

    return from_bounds(left, bottom, right, top, width, height)


def sanitize(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z_\-\.]+", "_", name)


def get_years(time_vals) -> np.ndarray:
    years = []
    for t in time_vals:
        if hasattr(t, "year"):
            years.append(int(t.year))
        else:
            years.append(int(str(t)[:4]))
    return np.array(years, dtype=int)


def normalize_mkprism_name(name: str) -> str:
    s = Path(name).name
    for suf in (".tar.gz", ".tgz", ".tar", ".nc"):
        if s.endswith(suf):
            s = s[: -len(suf)]
            break
    if s.endswith("_nc"):
        s = s[:-3]
    return s


_MKPRISM_RE2 = re.compile(
    r"^MKPRISM_(MKPRISMv\d+_skorea)_([A-Za-z0-9]+)_(?:gridraw_)?yearly(?:_\d{4}_\d{4})?$"
)


def parse_mkprism_name(name: str):
    base = normalize_mkprism_name(name)
    m = _MKPRISM_RE2.match(base)
    if not m:
        return None, None
    product = m.group(1)
    var = m.group(2)
    folder_name = f"MKPRISM_{product}_{var}_yearly"
    file_prefix = f"{product}_{var}"
    return folder_name, file_prefix


def _is_safe_member(member: tarfile.TarInfo) -> bool:
    p = Path(member.name)
    if p.is_absolute():
        return False
    if ".." in p.parts:
        return False
    return True


def tar_basename_to_nc_name(tar_name: str) -> str:
    if tar_name.endswith("_nc.tar.gz"):
        return tar_name[:-10] + ".nc"
    if tar_name.endswith(".tar.gz"):
        return tar_name[:-7] + ".nc"
    if tar_name.endswith("_nc.tgz"):
        return tar_name[:-7] + ".nc"
    if tar_name.endswith(".tgz"):
        return tar_name[:-4] + ".nc"
    if tar_name.endswith("_nc.tar"):
        return tar_name[:-7] + ".nc"
    if tar_name.endswith(".tar"):
        return tar_name[:-4] + ".nc"
    return tar_name + ".nc"


def unpack_tars(dataset_dir: Path, raw_tar_dir: Path, tar_glob: str):
    raw_tar_dir.mkdir(parents=True, exist_ok=True)

    cand = sorted(dataset_dir.glob(tar_glob))
    tar_files = [
        p for p in cand
        if p.is_file() and (p.name.endswith((".tar", ".tar.gz", ".tgz", ".tar.xz", ".tar.bz2")))
    ]

    for tar_path in tar_files:
        nc_target_name = tar_basename_to_nc_name(tar_path.name)
        nc_target_path = dataset_dir / nc_target_name

        if nc_target_path.exists():
            dest = raw_tar_dir / tar_path.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(tar_path), str(dest))
            continue

        tmp_dir = dataset_dir / "__tmp_extract__" / tar_path.stem
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
        tmp_dir.mkdir(parents=True, exist_ok=True)

        extracted_nc = None
        with tarfile.open(tar_path, "r:*") as tf:
            members = [m for m in tf.getmembers() if m.isfile() and _is_safe_member(m)]
            nc_members = [m for m in members if m.name.lower().endswith(".nc")]
            if not nc_members:
                shutil.rmtree(tmp_dir, ignore_errors=True)
                continue

            for m in nc_members:
                tf.extract(m, path=tmp_dir)
                extracted_path = tmp_dir / m.name
                if extracted_path.exists():
                    extracted_nc = extracted_path
                    break

        if extracted_nc is None:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            continue

        nc_target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(extracted_nc), str(nc_target_path))

        dest = raw_tar_dir / tar_path.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(tar_path), str(dest))

        shutil.rmtree(tmp_dir.parent, ignore_errors=True)


def prep_da_for_geotiff(da: xr.DataArray, cfg: dict) -> xr.DataArray:
    dims_cfg = cfg["dims"]
    x_dim = pick_dim_name(list(da.dims), dims_cfg["x_candidates"]) or pick_dim_name(list(da.coords), dims_cfg["x_candidates"])
    y_dim = pick_dim_name(list(da.dims), dims_cfg["y_candidates"]) or pick_dim_name(list(da.coords), dims_cfg["y_candidates"])
    if x_dim is None or y_dim is None:
        raise ValueError(f"Could not find spatial x/y dimensions. dims={da.dims}, coords={list(da.coords)}")

    rename_map = {}
    if x_dim != "x":
        rename_map[x_dim] = "x"
    if y_dim != "y":
        rename_map[y_dim] = "y"
    if rename_map:
        da = da.rename(rename_map)

    y0 = float(da["y"].values[0])
    y1 = float(da["y"].values[-1])
    if y0 < y1:
        da = da.sortby("y", ascending=False)

    da = da.rio.set_spatial_dims(x_dim="x", y_dim="y")

    epsg4326 = cfg["crs"]["epsg4326"]
    if da.rio.crs is None:
        da = da.rio.write_crs(epsg4326)

    try:
        _ = da.rio.transform()
    except Exception:
        tr = infer_transform_from_1d_coords(da["x"].values, da["y"].values)
        if tr is None:
            raise ValueError("Failed to infer transform: x/y are not a regular 1D grid.")
        da = da.rio.write_transform(tr)

    da = da.transpose("y", "x", ...)
    return da


def write_geotiff_4326(da: xr.DataArray, out_path: Path, cfg: dict):
    exp = cfg["export"]
    geotiff = cfg.get("geotiff", {})

    nodata = exp.get("nodata")
    if nodata is not None:
        da = da.fillna(nodata)
        da = da.rio.write_nodata(nodata)

    dtype = exp.get("dtype")
    if dtype:
        da = da.astype(dtype)

    kwargs = {}
    kwargs["compress"] = geotiff.get("compress", "deflate")
    kwargs["tiled"] = bool(geotiff.get("tiled", True))
    zlevel = geotiff.get("zlevel", 6)
    if zlevel is not None:
        kwargs["zlevel"] = int(zlevel)
    kwargs["BIGTIFF"] = "YES"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    da.rio.to_raster(str(out_path), **kwargs)


def warp_resample_tif(in_tif: Path, out_tif: Path, cfg: dict):
    crs_cfg = cfg["crs"]
    dst_crs = crs_cfg["utm52n"]
    resolution_m = float(crs_cfg["utm52n_res_m"])
    resampling_name = crs_cfg.get("utm52n_resampling", "bilinear").lower()
    num_threads = int(crs_cfg.get("num_threads", 4))

    resampling_map = {
        "nearest": Resampling.nearest,
        "bilinear": Resampling.bilinear,
        "cubic": Resampling.cubic,
        "average": Resampling.average,
        "mode": Resampling.mode,
        "lanczos": Resampling.lanczos,
    }
    rs = resampling_map.get(resampling_name)
    if rs is None:
        raise ValueError(f"Unsupported resampling method: {resampling_name}")

    out_tif.parent.mkdir(parents=True, exist_ok=True)

    with rasterio.open(in_tif) as src:
        if src.crs is None:
            raise ValueError(f"Input GeoTIFF has no CRS: {in_tif}")

        dst_transform, dst_width, dst_height = calculate_default_transform(
            src.crs, dst_crs, src.width, src.height, *src.bounds, resolution=resolution_m
        )

        nodata = src.nodata
        profile = src.profile.copy()
        profile.update(
            crs=dst_crs,
            transform=dst_transform,
            width=dst_width,
            height=dst_height,
            nodata=nodata,
            compress="deflate",
            tiled=True,
            BIGTIFF="YES",
        )

        with rasterio.open(out_tif, "w", **profile) as dst:
            for b in range(1, src.count + 1):
                reproject(
                    source=rasterio.band(src, b),
                    destination=rasterio.band(dst, b),
                    src_transform=src.transform,
                    src_crs=src.crs,
                    src_nodata=nodata,
                    dst_transform=dst_transform,
                    dst_crs=dst_crs,
                    dst_nodata=nodata,
                    resampling=rs,
                    num_threads=num_threads,
                )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--from", dest="from_year", type=int, default=None)
    ap.add_argument("--to", dest="to_year", type=int, default=None)
    args = ap.parse_args()

    cfg_path = Path(args.config).resolve()
    cfg = load_cfg(cfg_path)

    base_dir = Path(__file__).resolve().parent # fix the parent directory as the base

    paths = cfg["paths"]
    dataset_dir = resolve_path(paths["dataset_dir"], base_dir)
    raw_tar_dir = resolve_path(paths["raw_tar_dir"], base_dir)
    out_4326_root = resolve_path(paths["processed_epsg4326_dir"], base_dir)
    out_utm_root = resolve_path(paths["processed_utm52n_30m_dir"], base_dir)

    patt = cfg.get("patterns", {})
    tar_glob = patt.get("tar_glob", "*.tar*")
    nc_glob = patt.get("nc_glob", "*.nc")
    name_contains = patt.get("file_name_contains", "")

    unpack_tars(dataset_dir, raw_tar_dir, tar_glob)

    nc_files = sorted(dataset_dir.glob(nc_glob))
    if name_contains:
        nc_files = [p for p in nc_files if name_contains in p.name]
    if not nc_files:
        raise SystemExit(f"No .nc files to process in: {dataset_dir}")

    cand = sorted(dataset_dir.glob(tar_glob))
    tar_files = [
        p for p in cand
        if p.is_file() and p.name.endswith((".tar", ".tar.gz", ".tgz", ".tar.xz", ".tar.bz2"))
    ]

    if tar_files:
        unpack_tars(dataset_dir, raw_tar_dir, tar_glob)

    nc_files = sorted(dataset_dir.glob(nc_glob))
    if name_contains:
        nc_files = [p for p in nc_files if name_contains in p.name]
    if not nc_files:
        raise SystemExit(f"No .nc files to process in: {dataset_dir}")

    time_dim = cfg["dims"].get("time_dim", "time")

    epsg4326_code = cfg["crs"]["epsg4326_code"]
    epsg4326_res_tag = cfg["crs"]["epsg4326_res_tag"]
    utm_code = cfg["crs"]["utm52n_code"]
    utm_res_tag = cfg["crs"]["utm52n_res_tag"]

    for nc_path in nc_files:
        base_folder_name, file_prefix = parse_mkprism_name(nc_path.name)
        if base_folder_name is None or file_prefix is None:
            stem = nc_path.stem
            base_folder_name = stem
            file_prefix = stem

        ds = xr.open_dataset(nc_path)

        if time_dim not in ds.coords:
            ds.close()
            raise ValueError(f"No time coordinate '{time_dim}' found in: {nc_path.name}")

        years = get_years(ds[time_dim].values)
        y_from = args.from_year if args.from_year is not None else int(years.min())
        y_to = args.to_year if args.to_year is not None else int(years.max())

        if y_from > y_to:
            ds.close()
            raise ValueError(f"Invalid year range: from={y_from} is greater than to={y_to}.")

        folder_name = f"{base_folder_name}_{y_from}_{y_to}"

        idx_list = np.where((years >= y_from) & (years <= y_to))[0].tolist()
        if not idx_list:
            ds.close()
            raise ValueError(
                f"{nc_path.name}: No time steps found in the requested range ({y_from}..{y_to}). "
                f"Available range is {years.min()}..{years.max()}."
            )

        for v in list(ds.data_vars):
            da = ds[v]
            out_4326_dir = out_4326_root / folder_name
            out_utm_dir = out_utm_root / folder_name

            for ti in idx_list:
                year = int(years[ti])

                da_i = da.isel({time_dim: ti}).squeeze(drop=True)
                da_i = prep_da_for_geotiff(da_i, cfg)

                out_4326_tif = out_4326_dir / f"{sanitize(file_prefix)}_{year}_{epsg4326_code}_{epsg4326_res_tag}.tif"
                write_geotiff_4326(da_i, out_4326_tif, cfg)

                out_utm_tif = out_utm_dir / f"{sanitize(file_prefix)}_{year}_{utm_code}_{utm_res_tag}.tif"
                warp_resample_tif(out_4326_tif, out_utm_tif, cfg)

        ds.close()


if __name__ == "__main__":
    main()
