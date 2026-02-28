import fitz  # PyMuPDF
import os
import re
import argparse
import colorsys
import numpy as np
from osgeo import gdal, osr
import cv2
import traceback
import json


def parse_dms_to_dd(dms_string):
    """
    Converts a string in 'Degrees Decimal_Minutes' format (e.g., "47 30.5")
    to decimal degrees.
    """
    try:
        parts = dms_string.strip().split()
        if len(parts) != 2:
            raise ValueError("Input must be in 'DD MM.MM' format.")

        degrees = float(parts[0])
        minutes = float(parts[1])

        if degrees < 0:
            decimal_degrees = degrees - (minutes / 60.0)
        else:
            decimal_degrees = degrees + (minutes / 60.0)

        return decimal_degrees
    except (ValueError, IndexError) as e:
        raise ValueError(f"Invalid coordinate format: '{dms_string}'. Please use 'DD MM.MM'.") from e


def dd_to_dms_string(lat, lon):
    """Converts lat/lon from decimal degrees to a DMS string like '47 01 00 08 02 00'."""
    def convert(dd):
        dd = abs(dd)
        degrees = int(dd)
        minutes_float = (dd - degrees) * 60
        minutes = int(minutes_float)
        seconds = int((minutes_float - minutes) * 60)
        return f"{degrees:02d} {minutes:02d} {seconds:02d}"

    lat_str = convert(lat)
    lon_str = convert(lon)
    return f"{lat_str} {lon_str}"


class GeoreferenceEditor:
    def __init__(self, pdf_path, full_id, layout_rect, existing_points=None):
        """Initialize the GeoreferenceEditor.

        Args:
            pdf_path (str): The file path to the source PDF document.
            full_id (str): The unique identifier for the chart being edited.
            layout_rect (fitz.Rect): The rectangle defining the crop area.
            existing_points (list, optional): A list of pre-existing georeference points.
                                              Defaults to None.
        """
        self.pdf_path = pdf_path
        self.full_id = full_id
        self.layout_rect = layout_rect
        self.points = existing_points[:] if existing_points else []

        self.base_img = None
        self.window_name = f"Georef: {full_id} | 'q' Save | 'c' Clear"
        self.mouse_pos = (0, 0)
        self.crosshair_angle = 0.0
        self.crosshair_rotation_step = 0.1 # Degrees

        # Scaling state for coordinate mapping
        self.scale = 1.0
        self.offset_x = 0
        self.offset_y = 0

    def _mouse_callback(self, event, x, y, flags, param):
        """Handle mouse events for the OpenCV window.

        This callback manages mouse position tracking, crosshair rotation via the
        mouse wheel, and capturing points on left-click.

        Args:
            event (int): The type of OpenCV mouse event.
            x (int): The x-coordinate of the mouse cursor.
            y (int): The y-coordinate of the mouse cursor.
            flags (int): Additional flags for the event (e.g., mouse button state).
            param: Additional parameters passed by OpenCV.
        """
        self.mouse_pos = (x, y)

        # Handle mouse wheel rotation for crosshairs
        if event == cv2.EVENT_MOUSEWHEEL:
            if flags > 0: # Scroll up
                self.crosshair_angle += self.crosshair_rotation_step
            else: # Scroll down
                self.crosshair_angle -= self.crosshair_rotation_step

        if event == cv2.EVENT_LBUTTONDOWN:
            real_x = (x - self.offset_x) / self.scale
            real_y = (y - self.offset_y) / self.scale

            # Check if click is inside the image bounds
            if 0 <= real_x < self.base_img.shape[1] and 0 <= real_y < self.base_img.shape[0]:
                print(f"\n--- Point {len(self.points)+1} captured ---")
                lon_str_to_save, lat_str_to_save = None, None

                while lon_str_to_save is None:
                    try:
                        lon_str = input("Enter Longitude (X) in 'DD MM.MM' format: ")
                        parse_dms_to_dd(lon_str) # Validate format
                        lon_str_to_save = lon_str
                    except ValueError as e:
                        print(f"Error: {e}")

                while lat_str_to_save is None:
                    try:
                        lat_str = input("Enter Latitude (Y) in 'DD MM.MM' format: ")
                        parse_dms_to_dd(lat_str) # Validate format
                        lat_str_to_save = lat_str
                    except ValueError as e:
                        print(f"Error: {e}")

                # Subtract the margin to make coordinates relative to the crop_rect
                margin_px = 10 * 4.0 # 10pt margin * 4.0 zoom
                saved_x = real_x - margin_px
                saved_y = real_y - margin_px
                self.points.append({
                    "px": [round(saved_x, 2), round(saved_y, 2)],
                    "world": [lon_str_to_save, lat_str_to_save]
                })

    def run(self):
        """Run the interactive georeferencing session.

        This method opens the PDF, renders the specified chart area in an
        OpenCV window, and enters a loop to handle user input for placing
        and defining georeference points.

        Returns:
            list: A list of the georeference points collected during the session.
        """
        doc = fitz.open(self.pdf_path)
        # Render the crop_rect with a 10pt margin to see surrounding area
        margin = 10
        render_rect = self.layout_rect + (-margin, -margin, margin, margin)
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(4.0, 4.0), clip=render_rect)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, 3)
        self.base_img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        doc.close()

        cv2.namedWindow(self.window_name, cv2.WINDOW_NORMAL)
        # Attempt to maximize window (large size usually triggers OS maximize)
        cv2.resizeWindow(self.window_name, 2000, 2000)
        cv2.setMouseCallback(self.window_name, self._mouse_callback)

        print(f"Opening {self.full_id}...")

        while True:
            # 1. Get current window dimensions for canvas
            _, _, win_w, win_h = cv2.getWindowImageRect(self.window_name)
            if win_w <= 0 or win_h <= 0: win_w, win_h = 1000, 1000

            # 2. Calculate aspect-ratio preserving scale
            img_h, img_w = self.base_img.shape[:2]
            self.scale = min(win_w / img_w, win_h / img_h)

            new_w = int(img_w * self.scale)
            new_h = int(img_h * self.scale)

            # 3. Create the display frame (Letterboxed)
            canvas = np.zeros((win_h, win_w, 3), dtype=np.uint8)
            self.offset_x = (win_w - new_w) // 2
            self.offset_y = (win_h - new_h) // 2

            resized = cv2.resize(self.base_img, (new_w, new_h), interpolation=cv2.INTER_AREA)

            # --- Drawing overlays (points, borders, labels) ---
            # Draw the original crop rectangle (in blue) to show the actual boundary
            margin_px = margin * 4.0
            p1 = (int(margin_px * self.scale), int(margin_px * self.scale))
            p2 = (int((margin_px + self.layout_rect.width * 4.0) * self.scale), int((margin_px + self.layout_rect.height * 4.0) * self.scale))
            cv2.rectangle(resized, p1, p2, (255, 0, 0), 2) # Blue border

            for i, pt in enumerate(self.points):
                # Add margin back for display purposes
                display_x = (pt["px"][0] + margin_px) * self.scale
                display_y = (pt["px"][1] + margin_px) * self.scale
                px = (int(display_x), int(display_y))
                p1_marker = (px[0] - 2, px[1] - 2)
                p2_marker = (px[0] + 2, px[1] + 2)
                cv2.rectangle(resized, p1_marker, p2_marker, (0, 0, 255), -1) # BGR for Red, -1 for filled

                lon_val, lat_val = pt["world"]

                # --- Create a styled label with a semi-transparent background ---
                try:
                    lon_dd = lon_val if isinstance(lon_val, (int, float)) else parse_dms_to_dd(lon_val)
                    lat_dd = lat_val if isinstance(lat_val, (int, float)) else parse_dms_to_dd(lat_val)
                    label_text = f"P{i+1}: {dd_to_dms_string(lat_dd, lon_dd)}"
                except (ValueError, IndexError):
                    label_text = f"P{i+1}: Invalid Coords"
                font = cv2.FONT_HERSHEY_SIMPLEX
                font_scale = 0.6
                font_thickness = 1
                padding = 5

                (text_w, text_h), baseline = cv2.getTextSize(label_text, font, font_scale, font_thickness)

                # Define label box coordinates
                box_tl = (px[0] + 8, px[1] - text_h - padding)
                box_br = (px[0] + 8 + text_w + padding, px[1])

                # Clamp box coordinates to be within the image bounds to prevent errors
                box_tl = (max(0, box_tl[0]), max(0, box_tl[1]))
                box_br = (min(resized.shape[1], box_br[0]), min(resized.shape[0], box_br[1]))
                if box_tl[0] >= box_br[0] or box_tl[1] >= box_br[1]: continue # Skip if box is invalid

                # Create a copy of the ROI for blending
                sub_img = resized[box_tl[1]:box_br[1], box_tl[0]:box_br[0]]
                white_rect = np.ones(sub_img.shape, dtype=np.uint8) * 255
                res = cv2.addWeighted(sub_img, 0.2, white_rect, 0.8, 1.0)  # 80% opaque white
                resized[box_tl[1]:box_br[1], box_tl[0]:box_br[0]] = res

                cv2.rectangle(resized, box_tl, box_br, (0, 0, 0), 1) # Black border
                cv2.putText(resized, label_text, (box_tl[0] + padding, box_tl[1] + text_h + padding - baseline), font, font_scale, (0, 0, 0), font_thickness)

            # Place resized image onto canvas
            canvas[self.offset_y:self.offset_y+new_h, self.offset_x:self.offset_x+new_w] = resized

            # 4. Draw Rotated Crosshairs (Yellow lines)
            mx, my = self.mouse_pos
            length = max(win_w, win_h) * 2 # Make lines long enough to cover the screen
            rad = np.radians(self.crosshair_angle)
            cos_rad, sin_rad = np.cos(rad), np.sin(rad)

            # Horizontal line, rotated
            h_x1, h_y1 = int(mx - length/2 * cos_rad), int(my - length/2 * sin_rad)
            h_x2, h_y2 = int(mx + length/2 * cos_rad), int(my + length/2 * sin_rad)
            cv2.line(canvas, (h_x1, h_y1), (h_x2, h_y2), (0, 0, 255), 1)

            # Vertical line, rotated (by angle + 90 deg)
            v_x1, v_y1 = int(mx + length/2 * sin_rad), int(my - length/2 * cos_rad)
            v_x2, v_y2 = int(mx - length/2 * sin_rad), int(my + length/2 * cos_rad)
            cv2.line(canvas, (v_x1, v_y1), (v_x2, v_y2), (0, 0, 255), 1)

            # 5. Draw a small, fixed crosshair for precision clicking (White)
            precision_size = 10
            cv2.line(canvas, (mx - precision_size, my), (mx + precision_size, my), (255, 255, 255), 1)
            cv2.line(canvas, (mx, my - precision_size), (mx, my + precision_size), (255, 255, 255), 1)

            cv2.imshow(self.window_name, canvas)

            key = cv2.waitKey(20) & 0xFF
            if key == ord('q'): break
            if key == ord('c'): self.points = []

        cv2.destroyAllWindows()
        return self.points


def georeference(input_dir, config_file, filter_ids=None, force=False):
    """Start the interactive georeferencing process for charts.

    Iterates through charts defined in the config file, launching the
    GeoreferenceEditor for each one that needs points.

    Args:
        input_dir (str): Path to the directory containing PDF charts.
        config_file (str): Path to the JSON configuration file.
        filter_ids (list, optional): A list of strings to filter which chart IDs to process.
                                     Defaults to None.
        force (bool, optional): If True, re-georeference charts that already have points.
                                Defaults to False.
    """
    with open(config_file, 'r', encoding='utf-8') as f:
        config = json.load(f)

    layouts, mappings = load_config(config_file)
    pattern = re.compile(r"LS_ADINFO_0000_(.*)\.pdf")
    files = {re.search(pattern, f).group(1): f for f in os.listdir(input_dir) if re.search(pattern, f)}

    for full_id, mapping_data in mappings.items():
        if filter_ids and not any(f in full_id for f in filter_ids): continue
        # If --force is not used, skip items that already have enough points.
        if not force and "points" in mapping_data and len(mapping_data["points"]) >= 3:
            print(f"SKIPPING {full_id}: Already has {len(mapping_data['points'])} points. Use --force to re-georeference.")
            continue

        filename = files.get(full_id)
        if not filename: continue

        pdf_path = os.path.join(input_dir, filename)
        existing_points = mapping_data.get("points")
        editor = GeoreferenceEditor(pdf_path, full_id, layouts[mapping_data["layout"]], existing_points)
        new_points = editor.run()

        if new_points:
            config["mappings"][full_id]["points"] = new_points
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2)


def get_dynamic_color(index, total):
    """Generate a visually distinct color based on an index.

    Creates colors with evenly distributed hues in the HSV color space,
    useful for differentiating multiple items drawn on an image.

    Args:
        index (int): The zero-based index of the item.
        total (int): The total number of items.

    Returns:
        tuple: A tuple representing the RGB color (values from 0 to 1).
    """
    if total <= 1: return (1, 0, 0)
    hue = index / total
    return colorsys.hsv_to_rgb(hue, 1.0, 0.9)


def load_config(config_path):
    """Load and parse the JSON configuration file.

    Reads the config file, converts layout rectangles to fitz.Rect objects,
    and returns the layouts and mappings.

    Args:
        config_path (str): The path to the config.json file.

    Raises:
        FileNotFoundError: If the config file does not exist.

    Returns:
        tuple: A tuple containing:
            - dict: A dictionary of layout names to fitz.Rect objects.
            - dict: The raw mappings dictionary from the config file.
    """
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config file not found: {config_path}")
    with open(config_path, 'r') as f:
        data = json.load(f)
    layouts = {k: fitz.Rect(*v) for k, v in data['layouts'].items()}
    return layouts, data['mappings']


def crop_debug(input_dir, output_dir, config_file, filter_ids=None):
    """Generate debug images to visualize layout crop areas.

    Renders each PDF page and draws all defined layout rectangles on top of it,
    saving the result as a PNG. This is useful for verifying that the layout
    coordinates in config.json are correct.

    Args:
        input_dir (str): Path to the directory containing input PDF files.
        output_dir (str): Path to the directory where debug images will be saved.
        config_file (str): Path to the JSON configuration file.
        filter_ids (list, optional): A list of strings to filter which chart IDs to process.
                                     Defaults to None.
    """
    try:
        layouts, _ = load_config(config_file)
    except Exception as e:
        print(f"Error: {e}"); return

    debug_dir = os.path.join(output_dir, "Debug_Layouts")
    if not os.path.exists(debug_dir): os.makedirs(debug_dir)

    pattern = re.compile(r"LS_ADINFO_0000_(.*)\.pdf")
    layout_names = list(layouts.keys())
    total_types = len(layout_names)

    for filename in os.listdir(input_dir):
        match = pattern.search(filename)
        if not match: continue
        full_id = match.group(1)
        if filter_ids and not any(f in full_id for f in filter_ids): continue

        try:
            doc = fitz.open(os.path.join(input_dir, filename))
            page = doc[0]
            for i, name in enumerate(layout_names):
                rect = layouts[name]
                color = get_dynamic_color(i, total_types)
                dash_len = 10
                gap_len = dash_len * 0.5 * (total_types - 1)
                phase = i * dash_len

                page.draw_rect(rect, color=color, width=1, dashes=f"[{dash_len} {gap_len}] {phase}")

                label_rect = fitz.Rect(rect.x0 + 5, rect.y0 + 5 + (i * 20), rect.x0 + 100, rect.y0 + 22 + (i * 20))
                page.draw_rect(label_rect, color=None, fill=(1, 1, 1), fill_opacity=0.5)
                page.insert_textbox(label_rect, name, color=color, fontsize=10, align=fitz.TEXT_ALIGN_CENTER, fontname="helv")

            pix = page.get_pixmap(matrix=fitz.Matrix(4.0, 4.0), alpha=False)
            pix.save(os.path.join(debug_dir, filename.replace(".pdf", "_DEBUG.png")))
            print(f"DEBUG GENERATED: {full_id}")
            doc.close()
        except Exception as e:
            print(f"Error debugging {filename}: {e}")


def crop_png(input_dir, output_dir, config_file, filter_ids=None):
    """Render and save cropped chart areas as PNG files.

    For each chart defined in the mappings, this function crops the PDF page
    using the specified layout and saves the resulting image as a high-resolution
    PNG file.

    Args:
        input_dir (str): Path to the directory containing input PDF files.
        output_dir (str): Path to the directory where PNG files will be saved.
        config_file (str): Path to the JSON configuration file.
        filter_ids (list, optional): A list of strings to filter which chart IDs to process.
                                     Defaults to None.
    """
    try:
        layouts, mappings = load_config(config_file)
    except Exception as e:
        print(f"Error: {e}"); return

    output_dir = os.path.join(output_dir, "Rendered_Charts")
    if not os.path.exists(output_dir): os.makedirs(output_dir)
    pattern = re.compile(r"LS_ADINFO_0000_(.*)\.pdf")
    processed = 0

    for filename in os.listdir(input_dir):
        match = pattern.search(filename)
        if not match: continue
        full_id = match.group(1)
        if filter_ids and not any(f in full_id for f in filter_ids): continue
        if full_id not in mappings: continue

        # New Dictionary Format Only
        mapping_data = mappings[full_id]
        layout_name = mapping_data.get("layout")
        crop_rect = layouts.get(layout_name)

        if not crop_rect: continue
        try:
            doc = fitz.open(os.path.join(input_dir, filename))
            page = doc[0]
            pix = page.get_pixmap(matrix=fitz.Matrix(4.0, 4.0), clip=crop_rect, alpha=False)
            pix.save(os.path.join(output_dir, filename.replace(".pdf", ".png")))
            print(f"SUCCESS PNG: {full_id}")
            processed += 1
            doc.close()
        except Exception as e:
            print(f"ERROR: {e}")
    print(f"Finished. Processed {processed} files.")


def crop_geotiff(input_dir, output_dir, config_file, filter_ids=None):
    """Create georeferenced TIFF files from PDF charts.

    For each georeferenced chart, this function renders the cropped area from
    the PDF, applies a geospatial transformation based on the saved Ground
    Control Points (GCPs), and saves the result as a GeoTIFF file projected
    in EPSG:2056.

    Args:
        input_dir (str): Path to the directory containing input PDF files.
        output_dir (str): Path to the directory where GeoTIFF files will be saved.
        config_file (str): Path to the JSON configuration file.
        filter_ids (list, optional): A list of strings to filter which chart IDs to process.
                                     Defaults to None.
    """
    try:
        # Enable GDAL exceptions to make errors stop the script
        gdal.UseExceptions()

        layouts, mappings = load_config(config_file)
    except Exception as e:
        print(f"Error: {e}"); return

    output_dir = os.path.join(output_dir, "Geotiff_Charts")
    if not os.path.exists(output_dir): os.makedirs(output_dir)

    pattern = re.compile(r"LS_ADINFO_0000_(.*)\.pdf")
    zoom = 4.0

    for filename in os.listdir(input_dir):
        match = pattern.search(filename)
        if not match: continue
        full_id = match.group(1)

        if filter_ids and not any(f in full_id for f in filter_ids): continue
        if full_id not in mappings: continue

        mapping_data = mappings[full_id]
        points = mapping_data.get("points")

        # Enforce a minimum of 3 GCPs for a reliable polynomial transformation.
        if not points or len(points) < 3:
            print(f"SKIPPING {full_id}: Requires at least 3 georeference points, found {len(points) if points else 0}.")
            continue

        # A higher number of points is recommended for better accuracy with TPS.
        if len(points) < 6:
            print(f"WARNING {full_id}: Only {len(points)} GCPs found. For best results, provide at least 6 points.")

        layout_name = mapping_data.get("layout")
        crop_rect = layouts.get(layout_name)
        if not crop_rect: continue

        try:
            doc = fitz.open(os.path.join(input_dir, filename))
            page = doc[0]

            # 1. Render to RAM (NumPy array)
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=crop_rect, alpha=False)
            width, height = pix.width, pix.height

            # 2. Create GDAL Memory Dataset
            mem_driver = gdal.GetDriverByName('MEM')
            tmp_ds = mem_driver.Create('', width, height, 3, gdal.GDT_Byte)
            img_array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(height, width, 3)
            for i in range(3):
                tmp_ds.GetRasterBand(i + 1).WriteArray(img_array[:, :, i])

            # 3. Setup GCPs (Scaling config pixel coordinates by zoom)
            gcp_list = []
            for p in points:
                lon_dd = parse_dms_to_dd(p["world"][0])
                lat_dd = parse_dms_to_dd(p["world"][1])
                gcp_list.append(gdal.GCP(
                    lat_dd,      # world_y (Latitude)
                    lon_dd,      # world_x (Longitude)
                    0,           # world_z (Elevation)
                    p["px"][0],  # pixel_x
                    p["px"][1]   # pixel_y
                ))

            # Create a full SpatialReference object for the GCPs instead of a string
            srs = osr.SpatialReference()
            srs.ImportFromEPSG(4326)
            tmp_ds.SetGCPs(gcp_list, srs)

            # 4. Warp the image using the GCPs.
            #    We use a polynomial transformation which works reliably with >= 3 points.
            output_tif = os.path.join(output_dir, full_id + ".tif")

            gdal.Warp(
                output_tif,
                tmp_ds,
                srcNodata=0, # Treat black pixels in the source as transparent
                dstAlpha=True, # Create a transparency channel in the output
                dstSRS='EPSG:2056',
                resampleAlg=gdal.GRA_Cubic, # Use a high-quality resampler
                # Use Thin Plate Spline for a more accurate rubber-sheet transformation.
                transformerOptions=[
                    'SRC_METHOD=GCP_TPS'
                ]
            )

            print(f"SUCCESS GEOTIFF: {full_id}")
            tmp_ds = None
            doc.close()
        except Exception as e:
            print(f"--- ERROR PROCESSING {full_id} ---")
            print(f"Exception: {e}")
            traceback.print_exc()


def _create_georeferenced_memory_dataset(pdf_path, layout_rect, points, zoom=4.0):
    """
    Renders a PDF crop and creates a georeferenced GDAL in-memory dataset.
    Returns the dataset or None on error.
    """
    try:
        doc = fitz.open(pdf_path)
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=layout_rect, alpha=False)
        doc.close()

        mem_driver = gdal.GetDriverByName('MEM')
        tmp_ds = mem_driver.Create('', pix.width, pix.height, 3, gdal.GDT_Byte)
        img_array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, 3)
        for i in range(3):
            tmp_ds.GetRasterBand(i + 1).WriteArray(img_array[:, :, i])

            # This is the crucial step: explicitly mark black as nodata.
            tmp_ds.GetRasterBand(i + 1).SetNoDataValue(0)

        # The GCP coordinate order for gdal.GCP is (x, y, z, pixel, line)
        # Our 'world' is [lon, lat], so lon is x and lat is y.
        gcp_list = []
        for p in points:
            lon_dd = parse_dms_to_dd(p["world"][0])
            lat_dd = parse_dms_to_dd(p["world"][1])
            gcp_list.append(gdal.GCP(
                lat_dd,      # world_y (Latitude)
                lon_dd,      # world_x (Longitude)
                0,           # world_z (Elevation)
                p["px"][0],  # pixel_x
                p["px"][1]   # pixel_y
            ))
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(4326) # WGS84 for Lon/Lat
        tmp_ds.SetGCPs(gcp_list, srs)

        return tmp_ds
    except Exception as e:
        print(f"--- ERROR creating memory dataset for {os.path.basename(pdf_path)}: {e} ---")
        return None


def _build_mbtile_from_tiffs(tiff_files, mbtiles_path, temp_dir, min_zoom, max_zoom):
    """
    Builds a single MBTiles file from a list of GeoTIFF files.
    """
    # Step A: Build a VRT from the list of TIFFs for this group
    vrt_path = os.path.join(temp_dir, f"{os.path.basename(mbtiles_path)}.vrt")
    gdal.BuildVRT(vrt_path, tiff_files, srcNodata=0, VRTNodata=0)

    # Step B: Translate the VRT to an MBTiles file with the base (highest) zoom level
    print(f"  Creating base layer for zoom level {max_zoom}...")
    gdal.Translate(mbtiles_path,
                   vrt_path,
                   format="MBTILES",
                   creationOptions=[
                       f"MINZOOM={max_zoom}",
                       f"MAXZOOM={max_zoom}",
                       "TILE_FORMAT=PNG"
                   ])

    # Step C: Open the new MBTiles file and build the lower zoom levels
    print("  Building lower zoom levels (overviews)...")
    ds = gdal.Open(mbtiles_path, gdal.GA_Update)
    overview_factors = [2**i for i in range(1, max_zoom - min_zoom + 1)]
    if overview_factors:
        ds.BuildOverviews("CUBIC", overview_factors)
    ds = None
    print(f"  SUCCESS: Created {os.path.basename(mbtiles_path)}")


def create_mbtiles(vac_path, output_dir, config_file, filter_ids=None, min_zoom=12, max_zoom=14):
    """
    Renders charts from PDF and organizes them into specific MBTiles files based on their names.
    """
    try:
        gdal.UseExceptions()
        layouts, mappings = load_config(config_file)
    except Exception as e:
        print(f"Error: {e}"); return

    # 1. Setup output and temporary directories
    mbtiles_output_dir = os.path.join(output_dir, "MBtiles")
    os.makedirs(mbtiles_output_dir, exist_ok=True)

    temp_dir = os.path.join(mbtiles_output_dir, "_temp_tifs")
    if os.path.exists(temp_dir):
        import shutil; shutil.rmtree(temp_dir) # Clean up from previous runs
    os.makedirs(temp_dir)

    # 2. Render all relevant charts to temporary GeoTIFFs once
    rendered_charts = {} # Dict to store {full_id: temp_tif_path}
    pattern = re.compile(r"LS_ADINFO_0000_(.*)\.pdf")

    print("--- Step 1: Rendering all charts to temporary files ---")

    for filename in os.listdir(vac_path):
        match = pattern.search(filename)
        if not match: continue
        full_id = match.group(1)

        if filter_ids and not any(f in full_id for f in filter_ids): continue
        if full_id not in mappings: continue

        mapping_data = mappings[full_id]
        points = mapping_data.get("points")
        if not points or len(points) < 3: continue

        layout_name = mapping_data.get("layout")
        crop_rect = layouts.get(layout_name)
        if not crop_rect: continue

        pdf_path = os.path.join(vac_path, filename)
        tmp_ds = _create_georeferenced_memory_dataset(pdf_path, crop_rect, points)

        if tmp_ds:
            temp_tif_path = os.path.join(temp_dir, f"{full_id}.tif")
            gdal.Warp(temp_tif_path, tmp_ds,
                      dstSRS='EPSG:3857',
                      transformerOptions=['SRC_METHOD=GCP_TPS'],
                      dstAlpha=True,
                      resampleAlg=gdal.GRA_Cubic)
            rendered_charts[full_id] = temp_tif_path
            print(f"Rendered {full_id} to temporary file.")

    if not rendered_charts:
        print("No valid, georeferenced charts found to process. Exiting.")
        return

    # 3. Define the grouping logic and create MBTiles for each group
    print("\n--- Step 2: Grouping charts and creating MBTiles ---")
    groups = {
        "LS_VAC.mbtiles": lambda s: s.endswith("_VAC"),
        "LS_VAC_A.mbtiles": lambda s: s.endswith("_VAC_A"),
        "LS_VAC_D.mbtiles": lambda s: s.endswith("_VAC_D"),
        "LS_AREA.mbtiles": lambda s: s.endswith("_AREA") or s.endswith("_AREA_A") or s.endswith("_AREA_D"),
        "LS_AREA_A.mbtiles": lambda s: s.endswith("_AREA") or s.endswith("_AREA_A"),
        "LS_AREA_D.mbtiles": lambda s: s.endswith("_AREA") or s.endswith("_AREA_D"),
    }

    for mbtile_filename, condition in groups.items():
        group_files = [path for full_id, path in rendered_charts.items() if condition(full_id)]

        if not group_files:
            print(f"\nNo charts found for {mbtile_filename}. Skipping.")
            continue

        mbtiles_path = os.path.join(mbtiles_output_dir, mbtile_filename)
        print(f"\nCreating {mbtiles_path} from {len(group_files)} charts...")

        _build_mbtile_from_tiffs(group_files, mbtiles_path, temp_dir, min_zoom, max_zoom)

    # 4. Clean up
    print("\n--- Step 3: Cleaning up temporary files ---")
    try:
        import shutil
        shutil.rmtree(temp_dir)
    except Exception as e:
        print(f"Warning: Could not clean up temporary directory '{temp_dir}': {e}")

    print("\nAll MBTiles created successfully.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["crop_debug", "crop_png", "crop_geotiff", "georeference", "create_mbtiles"])
    parser.add_argument("--vac-path", default=r".\\")
    parser.add_argument("--output-path", default=r".\\")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--filter", nargs="+")
    parser.add_argument("--force", action="store_true", help="Force re-processing of items that already have points.")
    parser.add_argument("--min-zoom", type=int, default=12, help="Minimum zoom level for MBTiles.")
    parser.add_argument("--max-zoom", type=int, default=14, help="Maximum zoom level for MBTiles.")
    args = parser.parse_args()

    if args.mode == "crop_debug":
        crop_debug(args.vac_path, args.output_path, args.config, args.filter)
    elif args.mode == "crop_png":
        crop_png(args.vac_path, args.output_path, args.config, args.filter)
    elif args.mode == "crop_geotiff":
        crop_geotiff(args.vac_path, args.output_path, args.config, args.filter)
    elif args.mode == "georeference":
        georeference(args.vac_path, args.config, args.filter, args.force)
    elif args.mode == "create_mbtiles":
        create_mbtiles(args.vac_path, args.output_path, args.config, args.filter, args.min_zoom, args.max_zoom)
