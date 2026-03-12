# mk-prism

`mk-prism` converts yearly MK-PRISM South Korea climate archives into GeoTIFF products.

The script in [main.py](main.py) unpacks MK-PRISM NetCDF archives from `dataset/`, exports yearly rasters in `EPSG:4326`, and reprojects them to `EPSG:32652` at 30 m resolution.

## Current State

This repository currently contains:

- [main.py](main.py): the conversion pipeline
- [config.yaml](config.yaml): runtime paths, CRS settings, dimension names, and export options
- [environment.yml](environment.yml): Conda environment definition
- `dataset/` with four MK-PRISM yearly archive files for 2000-2019:
  - `MKPRISM_MKPRISMv21_skorea_TA_gridraw_yearly_2000_2019_nc.tar.gz`
  - `MKPRISM_MKPRISMv21_skorea_TAMIN_gridraw_yearly_2000_2019_nc.tar.gz`
  - `MKPRISM_MKPRISMv21_skorea_TAMAX_gridraw_yearly_2000_2019_nc.tar.gz`
  - `MKPRISM_MKPRISMv21_skorea_RN_gridraw_yearly_2000_2019_nc.tar.gz`

The repository does not currently include extracted `.nc` files or generated GeoTIFF outputs. Those are created when the pipeline runs.

## Runtime Outputs

When you run the converter, it creates and populates:

- `dataset/raw_data/`: original tar archives after extraction
- `processed_epsg4326/`: yearly GeoTIFF outputs in `EPSG:4326`
- `processed_utm52n_30m/`: yearly GeoTIFF outputs in `EPSG:32652` with 30 m resolution

## Variables

The bundled MK-PRISM archives cover four variables:

- `TA`: mean temperature
- `TAMIN`: minimum temperature
- `TAMAX`: maximum temperature
- `RN`: precipitation

## Environment

For public use, prefer the portable environment file:

```bash
conda env create -f environment-public.yml
conda activate mk-prism
```

The existing [environment.yml](environment.yml) is better treated as a fuller local export. It can still be useful for reproducing the original setup, but it is more platform-specific than most public users need.

If you want to use the exported environment instead, run:

```bash
conda env create -f environment.yml
conda activate aibio_310
```

If you keep [environment.yml](environment.yml) in the public repo, avoid reintroducing a machine-specific `prefix:` line.

## Usage

Run the full conversion pipeline:

```bash
python main.py --config config.yaml
```

Run only a subset of years:

```bash
python main.py --config config.yaml --from 2010 --to 2015
```

## Processing Steps

The pipeline does the following:

- scans `dataset/` for `*.tar*` and `*.nc` files
- safely extracts `.nc` members from tar archives
- moves processed tar files into `dataset/raw_data/`
- reads the configured time dimension (`time` by default)
- writes yearly GeoTIFF files in `EPSG:4326`
- reprojects each raster to `EPSG:32652`
- uses bilinear resampling at 30 m resolution
- writes `float32` outputs and fills missing values with `-99`
- writes tiled, deflate-compressed GeoTIFF files with `BIGTIFF=YES`

## Output Naming

Example geographic output:

```text
processed_epsg4326/MKPRISM_MKPRISMv21_skorea_TA_yearly_2010_2015/MKPRISMv21_skorea_TA_2015_4326_1e-2.tif
```

Example projected output:

```text
processed_utm52n_30m/MKPRISM_MKPRISMv21_skorea_TA_yearly_2010_2015/MKPRISMv21_skorea_TA_2015_32652_30m.tif
```

## Notes

- Paths in [config.yaml](config.yaml) are resolved relative to [main.py](main.py), not the shell working directory.
- Files are filtered by the `MKPRISM_MKPRISMv21_skorea_` prefix configured in [config.yaml](config.yaml).
- If extracted `.nc` files already exist in `dataset/`, the script skips re-extraction and moves matching tar files into `dataset/raw_data/`.

## License

Code and documentation in this repository are licensed under the MIT License. See [LICENSE](LICENSE).

The contents of `dataset/` are not automatically covered by the MIT License unless you explicitly have the right to release those files under MIT. If you plan to publish this repository, confirm the redistribution terms for the dataset archives or document a separate data license.

## Public Release Checklist

Before pushing this repository publicly, verify the following:

- confirm that you have the right to redistribute the files in `dataset/`
- if the dataset archives are public, check their internal metadata for usernames, internal paths, or processing history you do not want to expose
- prefer [environment-public.yml](environment-public.yml) for public setup instructions
- keep generated outputs out of version control via [.gitignore](.gitignore)
