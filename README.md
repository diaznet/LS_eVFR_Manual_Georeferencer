# Swiss eVFR Manual Georeferencer

This project provides a suite of tools to process Visual Approach Charts (VAC) and other materials from the Swiss eVFR PDF manual. The primary goal is to take specific chart areas from the PDF pages, georeference them using user-provided control points, and ultimately generate MBTiles files suitable for use in electronic flight bags (EFBs) and other mapping applications.

## Table of Contents

- [Core Features](#core-features)
- [Workflow & Usage](#workflow--usage)
  - [Setup](#setup)
  - [Configuration (`config.json`)](#configuration-configjson)
  - [Verify Layouts](#verify-layouts)
  - [Interactive Georeferencing](#interactive-georeferencing)
  - [Generate GeoTiffs](#generate-geotiffs)
  - [Generate MBTiles](#generate-mbtiles)
  - [Visualize Georeferencing Status](#visualize-georeferencing-status)
- [Command-Line Reference](#command-line-reference)
- [Chart Status](#chart-status)

## Core Features

*   **Interactive Georeferencing:** An OpenCV-based GUI to visually select points on a chart and assign real-world geographic coordinates.
*   **Flexible Layouts:** Define custom crop areas for different chart types within a JSON configuration file.
*   **PDF to GeoTIFF/MBTiles Conversion:** Uses GDAL for powerful geospatial transformations, including warping based on Ground Control Points (GCPs).
*   **Batch Processing:** Scripts are designed to process entire directories of PDF charts.
*   **Modular Workflow:** The process is broken down into distinct steps: debugging layouts, georeferencing, and creating map tiles.

## Workflow & Usage

The entire process is managed by the `LS_Georeferencer.py` script, which operates in several modes.

### Setup

*   **Prerequisites:**
    *   The Swiss eVFR Manual PDFs. This must be purchased and downloaded from the [Skybriefing Shop](https://www.skybriefing.com/fr/prices/evfr-manual). This repository **does not** provide the charts, as they are a paid product.
        > Note: if you do not have the eVFR Manual you can often download an more or less up to date version of the charts from the airport's own webiste, often in Briefing section.
    *   A Python environment (version 3.9 is used in the build workflow).
*   **Dependencies:** Install the required Python libraries.

    ```bash
    pip install -r requirements.txt
    ```

### Configuration (`config.json`)

This file is the heart of the operation.

*   `"layouts"`: An object where you define named rectangles (`[x0, y0, x1, y1]`) that correspond to the crop areas on the PDF page. The coordinates are in PDF points (1/72 inch).
*   `"mappings"`: An object that links a specific chart ID (e.g., "LSGB_VAC") to a layout and stores the georeferencing points collected during the interactive process.

#### The Concept of Layout Types

The Skyguide eVFR manual does not follow a consistent format for all its charts. PDFs can be in either portrait or landscape orientation, and the position and size of the actual chart area (the part to be georeferenced) can vary significantly from one document to another.

To handle this inconsistency, this project uses the concept of "Layout Types". Each `type_*` defined in the `"layouts"` section of `config.json` is a template that specifies a rectangular crop area (`[x0, y0, x1, y1]`). This rectangle isolates the map portion of the PDF page, ignoring headers, legends, and other marginalia.

When a chart in the `"mappings"` section is linked to a `"layout": "type_01"`, it tells the script to apply the `type_01` crop area to that specific PDF before any further processing. You can verify these areas using the `crop_debug` mode.

### Verify Layouts

> Optional but Recommended

Before spending time georeferencing, you can generate debug images to ensure your layout rectangles are correctly defined in `config.json`.

```bash
python LS_Georeferencer.py crop_debug --vac-path "path/to/pdfs" --output-path "path/to/output"
```

This will create PNG files in an `output/Debug_Layouts` folder, with all defined layouts drawn on them.

![Example Debug PNG](/assets/img/example_debug.png)

Optionnally you can generate PNG tiles for all cropped areas.

```bash
python LS_Georeferencer.py crop_png --vac-path "path/to/pdfs" --output-path "path/to/output"
```

This will create PNG files in an `output/Rendered_Charts` folder.

![Cropped PNG files](/assets/img/cropped_png.png)


### Interactive Georeferencing

This is the main manual step. The script will open each chart that needs georeferencing in an interactive window.

```bash
python LS_Georeferencer.py georeference --vac-path "path/to/pdfs"
```

*   **Operation:**
    *   The script iterates through the charts defined in `config.json`.
    *   It skips charts that already have 3 or more points unless you use the `--force` flag.
    *   A window opens showing the chart.
*   **Controls:**
    *   **Left-Click:** Click on a known point on the map (e.g., an intersection, a landmark, or a point on the lat/lon graticule).
    *   **Enter Coordinates:** After clicking, the console will prompt you to enter the Longitude (X) and Latitude (Y) for that point. The format must be `DD MM.MM` (e.g., `08 30.5` or `47 15.2`).
    *   **Mouse Wheel:** Rotates the crosshairs to help align with map features.
    *   **`c` key:** Clears all points for the current chart if you want to start over.
    *   **`q` key:** Saves the points you've added for the current chart and moves to the next one.
*   **Tip:** A minimum of 3 points is required for a basic transformation, but **6 or more points** spread across the chart are recommended for better accuracy.

![Example Georeferencing](/assets/img/example_georeferencing.png)


### Generate GeoTiffs

> Optional, for debugging

This step runs the `crop_geotiff` mode, which processes individual georeferenced charts and saves them as [georeferenced TIFF](https://en.wikipedia.org/wiki/GeoTIFF) files (`.tif`). It applies the transformation using the stored Ground Control Points (GCPs) for each chart.

```bash
python LS_Georeferencer.py crop_geotiff --vac-path "path/to/pdfs" --output-path "path/to/output"
```

![Cropped TIFF files](/assets/img/cropped_geotiffs.png)

Note that you can use for example QGIS to load these GeoTiffs tiles and troubleshooting potential georeferencing issues.

### Generate MBTiles

Once your charts are georeferenced, you can generate the final MBTiles files. This mode renders the charts, warps them using the saved points, and packages them into tiled datasets.

```bash
python LS_Georeferencer.py create_mbtiles --vac-path "path/to/pdfs" --output-path "path/to/output" --min-zoom 12 --max-zoom 14
```

*   The script first renders all georeferenced charts into temporary GeoTIFFs.
*   It then groups these GeoTIFFs based on naming conventions (e.g., all `_VAC` charts go together).
*   Finally, it uses GDAL's Python API to create an MBTiles file for each group, complete with overviews for the specified zoom levels.

![MBTiles](/assets/img/mbtiles.png)

### Visualize Georeferencing Status

To get a quick visual overview of which airports have been georeferenced, you can generate a status map.

This requires a GeoJSON file containing the coordinates for the airports, which can be downloaded from OpenAIP.

```bash
python LS_Georeferencer.py map_status --output-path "path/to/output" --map-filename "status.png" --geojson-path "path/to/ch_apt.geojson"
```

This command creates a PNG image showing a map of Switzerland with a green dot for each georeferenced airport and a red dot for each one that is missing georeferencing data.

See [below](#chart-status) for the current map.

## Command-Line Reference

```
usage: LS_Georeferencer.py [-h] [--vac-path VAC_PATH] [--output-path OUTPUT_PATH] [--config CONFIG] [--filter FILTER [FILTER ...]] [--force] [--min-zoom MIN_ZOOM] [--max-zoom MAX_ZOOM] [--map-filename MAP_FILENAME] [--geojson-path GEOJSON_PATH] [--outline-tif OUTLINE_TIF]
                           {crop_debug,crop_png,crop_geotiff,georeference,create_mbtiles,map_status}

positional arguments:
  {crop_debug,crop_png,crop_geotiff,georeference,create_mbtiles,map_status}

options:
  -h, --help            show this help message and exit
  --vac-path VAC_PATH   Path to the directory containing the input PDF files.
  --output-path OUTPUT_PATH
                        Path to the directory where output files will be saved.
  --config CONFIG       Path to the JSON configuration file.
  --filter FILTER [FILTER ...]
                        One or more strings to filter which chart IDs to process.
  --force               Force re-processing of items that already have points.
  --min-zoom MIN_ZOOM   Minimum zoom level for MBTiles.
  --max-zoom MAX_ZOOM   Maximum zoom level for MBTiles.
  --map-filename MAP_FILENAME
                        Output filename for the status map.
  --geojson-path GEOJSON_PATH
                        Path to a GeoJSON file with airport coordinates, for use with 'map_status' mode.
  --outline-tif OUTLINE_TIF
                        Path to a georeferenced TIF file to use as a map background for 'map_status' mode.
```

## Chart Status

This maps and the table below provide an overview of the georeferencing status for each chart defined in `config.json`. A chart is considered "Georeferenced" if it has at least 3 Ground Control Points (GCPs). The AIRAC column indicates the cycle for which the georeferencing was performed.

Pull requests are welcome.

![Georeferencing Status Map](/assets/img/georeferencing_status.png)


| Chart ID      | Layout Type | Georeferenced | AIRAC |
|---------------|-------------|---------------|-------|
| LSGB_VAC      | type_04     | Yes           | 2602  |
| LSGC_AREA     | type_03     | Yes           | 2602  |
| LSGC_VAC      | type_01     | Yes           | 2602  |
| LSGE_VAC      | type_04     | No            |       |
| LSGG_AREA_A   | type_02     | No            |       |
| LSGG_AREA_D   | type_02     | No            |       |
| LSGG_VAC_A    | type_01     | No            |       |
| LSGG_VAC_D    | type_01     | No            |       |
| LSGK_VAC      | type_02     | No            |       |
| LSGL_VAC      | type_03     | No            |       |
| LSGN_VAC      | type_04     | No            |       |
| LSGP_VAC      | type_04     | No            |       |
| LSGR_VAC      | type_04     | No            |       |
| LSGS_AREA     | type_02     | Yes           | 2602  |
| LSGS_VAC      | type_06     | Yes           | 2602  |
| LSGT_VAC      | type_04     | No            |       |
| LSGY_VAC      | type_04     | No            |       |
| LSMP_AREA     | type_03     | Yes           | 2602  |
| LSMP_VAC      | type_01     | Yes           | 2602  |
| LSPA_VAC      | type_04     | No            |       |
| LSPD_VAC      | type_04     | No            |       |
| LSPF_VAC      | type_04     | No            |       |
| LSPH_VAC      | type_04     | No            |       |
| LSPL_VAC      | type_04     | Yes           | 2602  |
| LSPM_VAC      | type_05     | Yes           | 2602  |
| LSPN_VAC      | type_04     | Yes           | 2602  |
| LSPO_VAC      | type_04     | Yes           | 2602  |
| LSPR_VAC      | type_04     | Yes           | 2602  |
| LSPU_VAC      | type_04     | Yes           | 2602  |
| LSPV_VAC      | type_04     | No            |       |
| LSTA_VAC      | type_04     | Yes           | 2602  |
| LSTB_VAC      | type_04     | No            |       |
| LSTO_VAC      | type_04     | No            |       |
| LSTR_VAC      | type_04     | No            |       |
| LSTZ_VAC      | type_04     | No            |       |
| LSZA_AREA     | type_03     | Yes           | 2602  |
| LSZA_VAC      | type_01     | Yes           | 2602  |
| LSZB_AREA     | type_03     | Yes           | 2602  |
| LSZB_VAC_A    | type_07     | Yes           | 2602  |
| LSZB_VAC_D    | type_08     | Yes           | 2602  |
| LSZC_AREA     | type_03     | Yes           | 2602  |
| LSZC_VAC      | type_01     | Yes           | 2602  |
| LSZE_VAC      | type_04     | No            |       |
| LSZF_VAC      | type_08     | No            |       |
| LSZG_AREA_A   | type_12     | No            |       |
| LSZG_AREA_D   | type_12     | No            |       |
| LSZG_VAC_A    | type_06     | No            |       |
| LSZG_VAC_D    | type_06     | No            |       |
| LSZH_AREA_A   | type_02     | Yes           | 2602  |
| LSZH_AREA_D   | type_02     | Yes           | 2602  |
| LSZH_VAC_A    | type_01     | Yes           | 2602  |
| LSZH_VAC_D    | type_01     | Yes           | 2602  |
| LSZI_VAC      | type_04     | No            |       |
| LSZJ_VAC      | type_04     | No            |       |
| LSZK_VAC      | type_04     | No            |       |
| LSZL_AREA     | type_11     | Yes           | 2602  |
| LSZL_VAC      | type_01     | Yes           | 2602  |
| LSZM_VAC      | type_03     | No            |       |
| LSZM_VAC_A    | type_03     | No            |       |
| LSZM_VAC_D    | type_01     | No            |       |
| LSZN_VAC      | type_03     | Yes           | 2602  |
| LSZO_VAC      | type_04     | Yes           | 2602  |
| LSZP_VAC      | type_04     | No            |       |
| LSZQ_VAC      | type_04     | No            |       |
| LSZR_AREA     | type_02     | Yes           | 2602  |
| LSZR_VAC      | type_06     | Yes           | 2602  |
| LSZS_AREA     | type_10     | Yes           | 2602  |
| LSZS_VAC      | type_09     | Yes           | 2602  |
| LSZT_VAC      | type_04     | No            |       |
| LSZU_VAC      | type_04     | Yes           | 2602  |
| LSZV_VAC      | type_04     | No            |       |
| LSZW_VAC      | type_04     | No            |       |
| LSZX_VAC      | type_04     | No            |       |
