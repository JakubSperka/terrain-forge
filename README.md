# Terrain Forge - GIS to 3D Print

Windows desktop prototype: GeoTIFF DEM/DTM + optional GeoPackage polygon/polyline layer → a multi-part 3MF terrain assembly.

## Run

On this computer, double-click **Start Terrain Forge.cmd** in this folder to open the app. The repaired environment uses Python installed inside `.python-runtime` rather than the user AppData folder. Keep that folder alongside `.venv`.

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then from this folder:

```powershell
uv sync --python 3.12
uv run terrain-forge
```

The first command installs Python and dependencies. This is a source application, not yet a packaged Windows installer.

## Workflow

1. Choose a GeoTIFF elevation raster, projected in meters (for example local UTM). The first raster band is used. Elevations default to meters; use 0.3048 for feet.
   For a large raster, optionally choose **AOI GeoPackage** and **AOI polygon layer**. All polygons in that layer are combined and reprojected to the DEM CRS. The model is clipped to their exact outlines, including holes; disconnected polygons produce separate pieces. Only the AOI's bounding region is sampled through a virtual raster, keeping the elevation array bounded by the selected sample count. Raster storage block size can still affect disk reads. Width/height and resolution refer to the AOI bounding box intersected with the DEM. NoData inside or immediately around the outline must be filled for boundary interpolation. Leave the AOI file empty to use the full DEM.
2. Set printed width, printed height, or a scale denominator (25000 means 1:25,000). The other dimension follows the raster footprint proportions.
3. Set vertical exaggeration and base thickness. The lowest sampled elevation sits at the base thickness; the model bottom is Z=0.
4. Optionally load a GeoPackage and select a polygon or polyline layer. Choose its operation, depth/height, floor thickness and line width, then click **Add layer**. Add another copy and change its file, layer or settings to process multiple layers. Selecting a table row loads it into the controls; edits update that selected row immediately. Vectors are reprojected to the DEM CRS. Polyline width is the full width in printed millimeters; polygons retain their footprint and holes.
5. Select an operation and export. Import the 3MF as one assembly with multiple parts and assign each part a filament in your slicer.

Before export, click **Calculate dimensions & scale** to see the final X/Y footprint, total Z height including the base and raised features, base/above-base breakdown, and horizontal and vertical scales. This builds the same solids used for export, including AOI clipping, without writing a 3MF. Complex models may take time. Export reuses that calculation while model settings and source files remain unchanged; edits mark it outdated.

**Save settings…** writes a versioned JSON file containing all parameters, file paths and layer selections. **Load settings…** restores them. Settings do not embed the source datasets or calculated meshes. File dialogs remember the last selected directory between app sessions; a populated file field still opens at its current path.

Feature rows can use the same or different GeoPackages, with up to 30 entries. The top row has highest priority: its complete footprint excludes lower-priority features, including when its operation is Cut only. Use **Move up/down** to change priority. All feature layers take priority over contours. Each layer operation is measured from the original terrain surface. Fully covered lower-priority entries are skipped with a progress message. With an empty list, the single feature fields are used for compatibility with earlier settings files; removing the last row clears those fields.

3MF objects and material resources are named **Terrain**, **Features: layer name**, and **Contours**. Repeated feature names gain a numeric suffix. Each feature layer receives a display color and remains a separate material part. Cut-only operations remove material and do not create an extra part. Tiles preserve these names; slicer UI support for imported names can vary.

## Print-bed grid

Choose **Export layout → Print-bed grid**, then enter bed width (X), bed depth (Y), edge clearance and maximum print height (Z). Usable tile size is the bed dimension minus clearance on both sides. Calculate dimensions to see the grid count and cell size. The model is split into equal cells without changing scale, removing material at seams, or shifting elevation. The height limit includes base and raised features; models exceeding it are rejected because this grid does not cut in Z.

For an output named `terrain.3mf`, grid export creates `terrain_tiles/`, containing one multicolor 3MF per occupied cell, `layout.json` with assembly offsets, a graphical **layout.pdf** assembly map, and `ASSEMBLY.txt`. Empty AOI cells are omitted. Row 01 is the south/bottom row, column 01 the west/left column. Import each tile as one assembly, assign filament by named material part, and position the assembly on your slicer bed. Clearance reserves room for brims/keep-out zones; it does not automatically position the tile on the bed. Existing tile folders are never overwritten; choose a new output name for another export. Output is staged until all tiles are complete. Maximum 400 grid cells per export.

Use **Layout JSON to PDF…** to create an assembly map from an earlier export without rebuilding meshes. The PDF shows occupied/empty cells, row/column IDs, north/east directions, tile dimensions, full filenames and assembly offsets. Large grids are divided into readable map panels and an automatically paginated file table. The map is schematic, not an exact terrain silhouette or a scale cutting template.

**Tile connectors → Underside keys** cuts matching sockets across internal tile seams and exports a separate `alignment-key.3mf`. Print the quantity listed in the PDF and `ASSEMBLY.txt`. Socket length, width, depth and fitting clearance are adjustable; clearance is applied per XY side and once in Z. Keys insert from underneath without increasing tile footprints. Test one fit before printing the full set; glue may be needed for retention. Positions lacking sufficient material or a 0.8 mm roof are skipped and reported. No seam gaps are added. Disconnected AOI pieces within a cell remain separate shells.

**Tile labels → Engraved / Embossed badge** adds row/column IDs and a north arrow to each tile underside. Embossed badges recess the background so the original flat bottom is retained. Text reads from below with grid north upward. Labels avoid sockets and unsafe thin areas; skipped placements are reported. These assembly options apply to Print-bed grid exports only. The PDF includes connector locations and quantities.

## Colored elevation contours

Choose **Contour lines → Enabled** to derive contour lines directly from the DEM, then select **Contour operation**: Flush inlay, Recessed channel, Raised feature, or Cut only. Set the elevation interval in ground meters, reference elevation (normally 0), line width and depth/raised height in printed millimeters. Recessed channels also use their own colored floor thickness. For example: interval 50 m, reference 0 m, width 0.6 mm, depth 0.4 mm. The total cut must be less than base thickness; raised heights are not limited by base thickness. Choose width/depth suitable for your nozzle and layer height. Contour settings are independent of feature-layer settings.

Levels are reference + integer multiples of interval, in the DEM's vertical datum after elevation-unit conversion. Vertical exaggeration does not change which elevations are contoured. Lines are traced on the same sampled triangles as the terrain and buffered to a constant horizontal width. The selected operation creates narrow colored inlays, recessed colored floors, raised ridges, or empty grooves. Assign a different filament to the named **Contours** part when importing the 3MF assembly; Cut only creates no contour material part.

Contour generation needs no GeoPackage. It can also run alongside feature layers: GeoPackage features take priority at crossings. Contours follow AOI clipping and are split along with print-bed tiles. Flush inlays preserve surface height and total volume; other operations change the surface as selected. Flat terrain or an interval with no crossings exports without a contour part. Closely spaced lines may merge if their width exceeds their separation. Sampling controls available terrain detail; contours do not recover detail absent from the sampled mesh. Up to 500 elevation levels are allowed per calculation. Older "Colored inlays" presets load as Enabled with Flush inlay.

**Major contours → Enabled** makes every Nth contour a major level, anchored to the reference elevation. Set its own width and depth/height; the selected contour operation applies to both classes. They export as **Contours: Major** and **Contours: Minor** for separate filament assignment. Major contours take priority over minor contours. Optional major elevation labels show ground elevation in metres and clear a small area around the text; labels without enough space are omitted. GeoPackage features retain priority over both classes.

## Small NoData holes

**NoData repair → Small enclosed holes** optionally interpolates missing elevations on the sampled terrain grid. Set the maximum connected hole size and search radius in sampled cells, not source raster pixels. Only small enclosed holes entirely within the required terrain region are eligible; edge-connected, large or unreachable holes remain unfilled and stop export if required. Existing valid sampled elevations and the source GeoTIFF are unchanged.

After calculating, **Show NoData repair map…** highlights repaired cells. Export also writes a georeferenced repair-mask TIFF, a PNG preview and a JSON report (beside a single model or inside the tile folder). The mask uses 0 for unchanged, 1 for repaired, and 2 for outside the required region. Changing sample count changes the size of a cell and therefore the repair limits. Repair is off by default.

| Operation | Terrain | Colored part |
|---|---|---|
| Flush inlay | Cut down by depth | Exact removed intersection; top flush with original terrain |
| Recessed channel | Cut down by depth + floor thickness | Floor top sits depth below original terrain |
| Raised feature | Original terrain | Feature follows terrain, extending upward by height |
| Cut only | Cut down by depth | No second part |

Depth and thickness are vertical Z distances in printed mm, independent of exaggeration. Colored parts share coordinates and do not overlap terrain volume. Disconnected river segments may be separate shells within one material object.

## Geometry and scale

For scale 1:S, horizontal mm per ground meter = 1000/S. Model Z = base + (elevation − minimum elevation) × elevation-unit factor × horizontal factor × exaggeration. The desktop reports final footprint and effective scale after export.

The grid uses bilinear resampling with the selected number of samples on its longest edge; boundary values are extended from the first interior samples. This approximates terrain and is not an exact preservation of every source pixel. Vector footprints are triangulated separately, rather than rasterized onto the DEM grid. Manifold boolean operations produce the cavity and colored solid. Cuts reaching the bottom are rejected.

3MF uses millimeters, named mesh resources, base-material display colors, and one component assembly. It stores geometry/material identity, not a printer-specific slicer project or guaranteed spool mapping. Slicer compatibility needs testing with your chosen slicer. Choose adequate feature widths and thicknesses for your printer.

## Current scope

- One DEM, an optional polygon AOI layer, and up to 30 feature-layer entries per export. AOI and features may come from the same or different GeoPackages.
- No interactive map drawing, terrain preview, or installer yet.
- DEM must be north-up in a projected meter CRS. Geographic/foot-based DEMs, rotated rasters, and NoData regions outside the optional small-hole repair limits need preprocessing in GIS.
- CRS metadata describes horizontal units; the elevation conversion is explicit because raster elevation units are often missing.
- Large grids and complex vectors can take substantial time and memory. Start at 160 samples and increase as necessary (maximum 800).
- Fixed earth/blue material display colors; filament selection occurs in the slicer.
- Feature and contour inserts target simultaneous multicolor printing and have no separate-part fit clearance. Underside alignment keys have their own adjustable fitting clearance.

## Development

```powershell
uv run pytest
```

Core dependencies: Rasterio for DEMs, GeoPandas/Shapely for vectors, Trimesh/Manifold for solids, PySide6 for desktop controls. Tests cover closed terrain, dimensions, exaggeration, feature modes, material assembly structure, polygon holes, NoData, and floor preservation.

References: [3MF Core](https://github.com/3MFConsortium/spec_core/blob/master/3MF%20Core%20Specification.md), [Trimesh booleans](https://trimesh.org/trimesh.boolean.html), [Rasterio reprojection](https://rasterio.readthedocs.io/en/stable/topics/reproject.html).
