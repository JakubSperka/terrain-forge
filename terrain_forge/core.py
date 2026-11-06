from dataclasses import dataclass, field, replace
from pathlib import Path
import math
import numpy as np
import rasterio
from rasterio.transform import from_bounds
from rasterio.warp import Resampling
from rasterio.vrt import WarpedVRT
from pyproj import CRS
import geopandas as gpd
from shapely import affinity, make_valid, union_all, intersects_xy, line_merge
from shapely.geometry import box, MultiLineString
import trimesh
from manifold3d import Manifold, Mesh64


@dataclass
class FeatureSettings:
    gpkg: str = ""
    layer: str = ""
    mode: str = "Flush inlay"
    depth: float = 0.8
    thickness: float = 0.6
    line_width: float = 1.2


@dataclass
class Settings:
    dem: str
    output: str
    size_mode: str = "Width (mm)"
    size: float = 180
    exaggeration: float = 1
    base: float = 3
    samples: int = 160
    elevation_factor: float = 1
    baseline_mode: str = 'Automatic minimum'
    baseline_elevation: float = 0
    gpkg: str = ""
    layer: str = ""
    mode: str = "Flush inlay"
    depth: float = 0.8
    thickness: float = 0.6
    line_width: float = 1.2
    aoi_gpkg: str = ""
    aoi_layer: str = ""
    export_mode: str = "Single model"
    bed_width: float = 220
    bed_depth: float = 220
    bed_margin: float = 5
    print_height: float = 250
    contours: str = "Off"
    contour_interval: float = 50
    contour_origin: float = 0
    contour_width: float = 0.6
    contour_depth: float = 0.4
    contour_operation: str = "Flush inlay"
    contour_thickness: float = 0.4
    feature_layers: list = field(default_factory=list)
    nodata_repair: str = 'Off'
    repair_max_cells: int = 16
    repair_radius: int = 5
    major_contours: str = 'Off'
    major_every: int = 5
    major_width: float = 1.0
    major_depth: float = 0.6
    major_labels: str = 'Off'
    major_label_height: float = 2.5
    tile_connectors: str = 'Off'
    key_length: float = 12
    key_width: float = 4
    key_depth: float = 1
    key_clearance: float = 0.2
    tile_labels: str = 'Off'
    tile_label_height: float = 3
    tile_label_depth: float = 0.4


def configured_features(s):
    if not isinstance(s.feature_layers,list):
        raise ValueError("Feature layers must be a list.")
    if len(s.feature_layers)>30:
        raise ValueError("Use at most 30 feature layers per model.")
    entries=s.feature_layers or ([dict(gpkg=s.gpkg,layer=s.layer,mode=s.mode,depth=s.depth,
                                      thickness=s.thickness,line_width=s.line_width)] if s.gpkg else [])
    result=[]
    for item in entries:
        try:
            layer=FeatureSettings(**item) if isinstance(item,dict) else item
        except TypeError as error:
            raise ValueError("Invalid feature layer settings.") from error
        if not isinstance(layer,FeatureSettings) or not isinstance(layer.gpkg,str) or not layer.gpkg:
            raise ValueError("Each feature layer needs a GeoPackage path.")
        if not isinstance(layer.layer,str) or not layer.layer:
            raise ValueError("Choose a layer for each feature entry.")
        if layer.mode not in ('Flush inlay','Recessed channel','Raised feature','Cut only'):
            raise ValueError(f"Unknown operation for {layer.layer}.")
        for key in ('depth','thickness','line_width'):
            v=getattr(layer,key)
            if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<=0:
                raise ValueError(f"{layer.layer}: {key} must be positive and finite.")
        cut=layer.depth+(layer.thickness if layer.mode=='Recessed channel' else 0)
        if layer.mode!='Raised feature' and cut>=s.base:
            raise ValueError(f"{layer.layer}: base thickness must exceed the total cut depth to preserve a solid floor.")
        result.append(layer)
    return result


def solid(z, width, height, bottom=0):
    """Closed height field; z rows run south to north."""
    rows, cols = z.shape
    x, y = np.meshgrid(np.linspace(0, width, cols), np.linspace(0, height, rows))
    top = np.column_stack((x.ravel(), y.ravel(), z.ravel()))
    low = top.copy()
    low[:, 2] = bottom
    count = len(top)
    faces = []
    for r in range(rows - 1):
        for c in range(cols - 1):
            a = r * cols + c
            b, d, e = a + 1, a + cols, a + cols + 1
            faces.extend([(a, b, e), (a, e, d), (a+count, e+count, b+count), (a+count, d+count, e+count)])
    boundary = list(range(cols))
    boundary += [r*cols+cols-1 for r in range(1, rows)]
    boundary += list(range(count-2, count-cols-1, -1))
    boundary += [r*cols for r in range(rows-2, 0, -1)]
    for a, b in zip(boundary, boundary[1:] + boundary[:1]):
        faces.extend([(a, a+count, b+count), (a, b+count, b)])
    mesh = trimesh.Trimesh(np.vstack((top, low)), faces, process=True)
    validate(mesh)
    return mesh


def validate(mesh):
    if mesh.is_empty or not mesh.is_watertight or not mesh.is_volume or mesh.volume <= 0:
        raise ValueError("Geometry is empty or not a closed printable solid. Adjust feature dimensions.")


def boolean(op, meshes, allow_empty=False):
    # Keep double precision across successive cuts sharing coincident surfaces.
    solids = []
    for m in meshes:
        native = m.metadata.get("manifold")
        if native is None:
            native = Manifold(Mesh64(np.array(m.vertices, dtype=np.float64, order="C", copy=True),
                                     np.array(m.faces, dtype=np.uint64, order="C", copy=True)))
            m.metadata["manifold"] = native
        solids.append(native)
    result = solids[0]
    for other in solids[1:]:
        if op == "difference":
            result = result - other
        elif op == "intersection":
            result = result ^ other
        elif op == "union":
            result = result + other
        else:
            raise ValueError(f"Unknown boolean operation: {op}")
    data = result.to_mesh64()
    native = result
    result = trimesh.Trimesh(data.vert_properties, data.tri_verts, process=False)
    result.metadata["manifold"] = native
    if not allow_empty:
        validate(result)
    return result


def load_aoi(s, crs, raster_bounds):
    if not s.aoi_gpkg:
        return None
    if not s.aoi_layer:
        raise ValueError("Select an AOI polygon layer.")
    frame = gpd.read_file(s.aoi_gpkg, layer=s.aoi_layer)
    if frame.crs is None:
        raise ValueError("AOI layer needs a coordinate reference system.")
    shapes = []
    for geometry in frame.to_crs(crs).geometry:
        if geometry is None or geometry.is_empty:
            continue
        if geometry.geom_type not in ("Polygon", "MultiPolygon"):
            raise ValueError("AOI layer must contain polygons only.")
        shapes.extend(polygons(make_valid(geometry)))
    footprint = union_all(shapes).intersection(box(*raster_bounds))
    footprint = union_all(list(polygons(footprint)))
    if footprint.is_empty or footprint.area <= 0:
        raise ValueError("AOI contains no polygon area overlapping the DEM.")
    return footprint


def dem_crs(raw_crs):
    """Recover a stripped GeoTIFF LOCAL_CS only from its explicit EPSG ID."""
    crs = CRS.from_wkt(raw_crs.to_wkt())
    if crs.is_engineering:
        authority = crs.to_json_dict().get("id", {})
        if authority.get("authority") == "EPSG":
            try:
                candidate = CRS.from_epsg(authority["code"])
            except (ValueError, KeyError):
                candidate = None
            if candidate is not None and candidate.is_projected:
                source_axes, target_axes = crs.axis_info, candidate.axis_info
                if len(source_axes) == len(target_axes) == 2 and all(
                    a.direction == b.direction and
                    math.isclose(a.unit_conversion_factor, b.unit_conversion_factor, rel_tol=1e-10)
                    for a,b in zip(source_axes, target_axes)
                ):
                    crs = candidate
    axes = crs.axis_info[:2]
    if not crs.is_projected or len(axes) != 2 or any(
        not math.isclose(a.unit_conversion_factor, 1, rel_tol=1e-8) for a in axes
    ):
        units = ", ".join(a.unit_name for a in axes) or "unknown"
        raise ValueError(
            f"DEM CRS read as '{crs.name}' ({crs.type_name}; units: {units}). "
            "A projected CRS with meter units is required. If the raster is already projected, "
            "repair its CRS metadata in GIS; otherwise reproject it."
        )
    return crs


def load_terrain(s, include_aoi=False, include_elevation=False, include_repair=False):
    for name in ("size", "exaggeration", "base", "elevation_factor", "depth", "thickness", "line_width"):
        if not math.isfinite(getattr(s, name)) or getattr(s, name) <= 0:
            raise ValueError(f"{name} must be positive and finite.")
    if not 8 <= s.samples <= 800:
        raise ValueError("Resolution must be between 8 and 800 samples on the longest edge.")
    if s.size_mode not in ("Width (mm)", "Height (mm)", "Scale denominator"):
        raise ValueError("Unknown size mode.")
    with rasterio.open(s.dem) as src:
        if not src.crs:
            raise ValueError("DEM needs a coordinate reference system.")
        crs = dem_crs(src.crs)
        if src.transform.b or src.transform.d:
            raise ValueError("Rotated rasters must first be warped to a north-up grid in GIS.")
        aoi = load_aoi(s, crs, src.bounds)
        west, south, east, north = src.bounds if aoi is None else aoi.bounds
        w, h = east-west, north-south
        if w <= 0 or h <= 0:
            raise ValueError("DEM must be a north-up raster.")
        factor = 1000/s.size if s.size_mode == "Scale denominator" else s.size/(w if s.size_mode == "Width (mm)" else h)
        cols, rows = max(2, round(s.samples*w/max(w,h))), max(2, round(s.samples*h/max(w,h)))
        # Destination sample centers include the source's outer footprint edges.
        dx, dy = w/(cols-1), h/(rows-1)
        transform = from_bounds(west-dx/2, south-dy/2, east+dx/2, north+dy/2, cols, rows)
        # A virtual destination grid lets GDAL read only source blocks needed for
        # this AOI, without allocating the full source raster in Python memory.
        with WarpedVRT(src, src_crs=crs.to_wkt(), crs=crs.to_wkt(), transform=transform, width=cols, height=rows,
                       dtype="float64", nodata=np.nan, resampling=Resampling.bilinear,
                       warp_mem_limit=64) as vrt:
            data = vrt.read(1, masked=True).filled(np.nan)
        # Edge samples can fall just outside the raster; use nearest interior values.
        data[0] = data[1]
        data[-1] = data[-2]
        data[:, 0] = data[:, 1]
        data[:, -1] = data[:, -2]
        required = np.ones(data.shape, dtype=bool)
        if aoi is not None:
            xs, ys = np.meshgrid(np.linspace(west, east, cols), np.linspace(north, south, rows))
            # Keep valid neighbors for triangles crossing the exact AOI boundary.
            required = intersects_xy(aoi.buffer(math.hypot(dx, dy)), xs, ys)
        repair_mask=np.zeros(data.shape,dtype=bool)
        repair_regions=[]
        if s.nodata_repair not in ('Off','Small enclosed holes'):
            raise ValueError('Unknown NoData repair mode.')
        if s.nodata_repair!='Off':
            from .repair import repair_small_holes
            data,repair_mask,repair_regions=repair_small_holes(data,required,s.repair_max_cells,s.repair_radius)
        if not np.isfinite(data[required]).all():
            remaining=int((~np.isfinite(data)&required).sum())
            raise ValueError(f"DEM contains NoData holes ({remaining} required sampled cells remain). "
                             "Enable small-hole repair, increase its limits for enclosed holes, or fill/crop the DEM in GIS. "
                             "Edge-connected gaps are never filled automatically.")
        data[~required] = data[required].min()
        data = np.flipud(data).astype(np.float64) * s.elevation_factor
        reference = terrain_reference(s, float(data.min()))
        z = s.base + (data-reference) * factor * s.exaggeration
    result = z, w*factor, h*factor, crs, (west, south, east, north), factor
    if include_aoi:
        result = (*result, aoi)
    if include_elevation:
        result = (*result, float(data.min()))
    if include_repair:
        for region in repair_regions:
            region['bounds_crs']=[west+region['col_min']*dx,south+(rows-1-region['row_max'])*dy,
                                  west+region['col_max']*dx,south+(rows-1-region['row_min'])*dy]
        report=dict(repaired_cells=int(repair_mask.sum()),regions=repair_regions,
                    sample_rows=rows,sample_columns=cols,crs=crs.to_string(),
                    bounds=[west,south,east,north],mask=repair_mask,required=required,
                    method='Inverse-distance interpolation; sampled grid; source DEM unchanged')
        result=(*result,report)
    return result


def polygons(geometry):
    if geometry.geom_type == "Polygon":
        yield geometry
    elif hasattr(geometry, "geoms"):
        for part in geometry.geoms:
            yield from polygons(part)


def feature_prism(s, crs, bounds, factor, ceiling):
    frame = gpd.read_file(s.gpkg, layer=s.layer or None)
    if frame.crs is None:
        raise ValueError("Vector layer needs a coordinate reference system.")
    frame = frame.to_crs(crs)
    shapes = []
    for geom in frame.geometry:
        if geom is None or geom.is_empty:
            continue
        geom = make_valid(geom)
        if geom.geom_type in ("LineString", "MultiLineString"):
            geom = geom.buffer(s.line_width/factor/2)
        elif geom.geom_type not in ("Polygon", "MultiPolygon"):
            raise ValueError("Use a polygon or polyline layer; unsupported or collapsed geometry found.")
        shapes.append(geom)
    footprint = union_all(shapes).intersection(box(*bounds))
    return footprint_prism(footprint, bounds, factor, ceiling)


def footprint_prism(footprint, bounds, factor, ceiling):
    footprint = affinity.translate(footprint, -bounds[0], -bounds[1])
    footprint = affinity.scale(footprint, xfact=factor, yfact=factor, origin=(0,0))
    pieces = [trimesh.creation.extrude_polygon(p, ceiling, engine="earcut") for p in polygons(footprint) if p.area > 1e-10]
    if not pieces:
        raise ValueError("No polygon or buffered line overlaps the DEM.")
    return pieces[0] if len(pieces) == 1 else boolean("union", pieces)


def terrain_reference(s, elevation_min):
    if s.baseline_mode == 'Automatic minimum':
        return elevation_min
    if s.baseline_mode != 'Custom elevation':
        raise ValueError('Unknown baseline mode.')
    if not math.isfinite(s.baseline_elevation) or s.baseline_elevation > elevation_min:
        raise ValueError(f'Reference elevation must be finite and at or below the sampled minimum ({elevation_min:.3f} m).')
    return s.baseline_elevation


def elevation_summary(s):
    """Read the sampled AOI without building solids or enforcing a custom baseline."""
    z, _, _, _, _, factor, minimum = load_terrain(replace(s, baseline_mode='Automatic minimum'), include_elevation=True)
    return dict(elevation_min=minimum, elevation_max=minimum+float(np.ptp(z))/(factor*s.exaggeration))


def contour_footprint(s, z, width, height, factor, elevation_min, kind='all', label_zones=None):
    """Trace isolines on the same triangles used by the printable terrain."""
    for name in ('contour_interval','contour_width','contour_depth'):
        if not math.isfinite(getattr(s,name)) or getattr(s,name) <= 0:
            raise ValueError(f"{name} must be positive and finite.")
    if not math.isfinite(s.contour_origin):
        raise ValueError("Contour reference elevation must be finite.")
    if s.contour_operation not in ('Flush inlay','Recessed channel','Raised feature','Cut only'):
        raise ValueError("Unknown contour operation.")
    if not math.isfinite(s.contour_thickness) or s.contour_thickness <= 0:
        raise ValueError("Contour floor thickness must be positive and finite.")
    cut=s.contour_depth+(s.contour_thickness if s.contour_operation=='Recessed channel' else 0)
    if s.contour_operation != 'Raised feature' and cut >= s.base:
        raise ValueError("Total contour cut depth must be smaller than the base thickness.")
    elevation_max = elevation_min + float(np.ptp(z))/(factor*s.exaggeration)
    epsilon=max(1e-8,max(abs(elevation_min),abs(elevation_max))*1e-12)
    if elevation_max-elevation_min <= epsilon:
        return None,0
    start = math.floor((elevation_min+epsilon-s.contour_origin)/s.contour_interval)+1
    stop = math.ceil((elevation_max-epsilon-s.contour_origin)/s.contour_interval)
    if stop-start > 500:
        raise ValueError("More than 500 contour levels requested. Increase the contour interval.")
    if stop <= start:
        return None, 0
    rows,cols=z.shape
    x,y=np.meshgrid(np.linspace(0,width,cols),np.linspace(0,height,rows))
    vertices=np.column_stack((x.ravel(),y.ravel(),z.ravel()))
    cells=np.arange(rows*cols).reshape(rows,cols)[:-1,:-1].ravel()
    faces=np.vstack((np.column_stack((cells,cells+1,cells+cols+1)),
                     np.column_stack((cells,cells+cols+1,cells+cols))))
    triangles=vertices[faces]
    lo,hi=triangles[:,:,2].min(axis=1),triangles[:,:,2].max(axis=1)
    bands=[]
    labels=[]
    count=0
    for index in range(start,stop):
        if kind=='major' and index%s.major_every!=0 or kind=='minor' and index%s.major_every==0:
            continue
        level=s.base+(s.contour_origin+index*s.contour_interval-terrain_reference(s,elevation_min))*factor*s.exaggeration
        selected=triangles[(lo<=level)&(hi>level)]
        if not len(selected):
            continue
        segments=np.empty((len(selected),2,2),dtype=float)
        filled=np.zeros(len(selected),dtype=int)
        for a,b in ((0,1),(1,2),(2,0)):
            za,zb=selected[:,a,2],selected[:,b,2]
            crossing=((za<=level)&(zb>level))|((zb<=level)&(za>level))
            ids=np.flatnonzero(crossing)
            t=(level-za[ids])/(zb[ids]-za[ids])
            segments[ids,filled[ids]]=selected[ids,a,:2]+t[:,None]*(selected[ids,b,:2]-selected[ids,a,:2])
            filled[ids]+=1
        segments=segments[(filled==2)&(np.linalg.norm(segments[:,1]-segments[:,0],axis=1)>1e-9)]
        if len(segments):
            lines=line_merge(union_all(MultiLineString(segments.tolist())))
            bands.append(lines.buffer(s.contour_width/2,quad_segs=3))
            if kind=='major' and s.major_labels=='Enabled':
                from .lettering import text_shape
                line=max(list(lines.geoms) if hasattr(lines,'geoms') else [lines],key=lambda g:g.length)
                point=line.interpolate(.5,normalized=True)
                glyph=text_shape(f'{s.contour_origin+index*s.contour_interval:g}',s.major_label_height)
                gx0,gy0,gx1,gy1=glyph.bounds
                glyph=affinity.translate(glyph,point.x-(gx0+gx1)/2,point.y-(gy0+gy1)/2)
                zone=box(*glyph.bounds).buffer(s.major_label_height*.25,join_style=2)
                if box(0,0,width,height).covers(zone) and (label_zones is None or not any(zone.intersects(other) for other in label_zones)):
                    labels.append((zone,glyph))
                    if label_zones is not None:
                        label_zones.append(zone)
            count+=1
    if not bands:
        return None,0
    footprint=union_all(bands)
    for zone,glyph in labels:
        footprint=footprint.difference(zone).union(glyph)
    return footprint.intersection(box(0,0,width,height)),count


def build_model(s, progress=lambda message: None):
    """Build without writing a file, for dimension checks and subsequent export."""
    progress("Reading elevation grid…")
    z, w, h, crs, bounds, factor, aoi, elevation_min, repair = load_terrain(s, include_aoi=True, include_elevation=True,include_repair=True)
    if s.nodata_repair!='Off':
        progress(f"NoData repair: {repair['repaired_cells']} sampled cells in {len(repair['regions'])} enclosed holes.")
    progress("Building terrain solid…")
    terrain = solid(z, w, h)
    parts = [("Terrain", "#BCA885FF", terrain)]
    layers=configured_features(s)
    ceiling=float(z.max()+max([s.contour_depth,s.major_depth if s.major_contours!='Off' else 0]+[layer.depth for layer in layers])+1)
    claimed=None
    used_names={}
    palette=['#258DCFFF','#D68A37FF','#769E49FF','#A16DC2FF','#D85F72FF','#49A5A0FF']
    for index,layer in enumerate(layers):
        progress(f"Processing feature layer {index+1}/{len(layers)}: {layer.layer}…")
        raw_prism = feature_prism(layer, crs, bounds, factor, ceiling)
        prism=raw_prism if claimed is None else boolean('difference',[raw_prism,claimed],allow_empty=True)
        claimed=raw_prism if claimed is None else boolean('union',[claimed,raw_prism])
        if prism.is_empty or prism.metadata.get('manifold') is not None and prism.metadata['manifold'].volume()<=1e-10:
            progress(f"Skipping {layer.layer}: its footprint is covered by higher-priority layers.")
            continue
        cut = layer.depth if layer.mode in ("Flush inlay", "Cut only") else layer.depth+layer.thickness
        progress("Computing solid intersections…")
        if layer.mode == "Raised feature":
            upper = solid(z+layer.depth, w, h)
            feature = boolean("intersection", [boolean("difference", [upper, terrain]), prism])
        else:
            floor = solid(z-cut, w, h)
            cavity = boolean("intersection", [boolean("difference", [terrain, floor]), prism])
            parts[0] = ("Terrain", "#BCA885FF", boolean("difference", [parts[0][2], cavity]))
            feature = cavity
            if layer.mode == "Recessed channel":
                lowered = solid(z-layer.depth, w, h)
                feature = boolean("intersection", [boolean("difference", [lowered, floor]), prism])
        if layer.mode != "Cut only":
            used_names[layer.layer]=used_names.get(layer.layer,0)+1
            suffix=f" ({used_names[layer.layer]})" if used_names[layer.layer]>1 else ''
            parts.append((f"Features: {layer.layer}{suffix}",palette[index%len(palette)],feature))
    if s.contours not in ('Off','Colored inlays','Enabled'):
        raise ValueError("Unknown contour mode.")
    contour_levels=0
    major_levels=0
    if s.contours != 'Off':
        if s.major_contours not in ('Off','Enabled') or s.major_labels not in ('Off','Enabled'):
            raise ValueError('Unknown major contour or elevation label mode.')
        if s.major_contours!='Off' and (type(s.major_every) is not int or not 2<=s.major_every<=100):
            raise ValueError('Major contour frequency must be an integer from 2 to 100.')
        if not math.isfinite(s.major_label_height) or s.major_label_height<=0:
            raise ValueError('Elevation label height must be positive.')
        progress("Tracing elevation contours and applying contour operation…")
    styles=[]
    if s.contours!='Off':
        styles=[(s,'all','Contours','#34312FFF')]
        if s.major_contours=='Enabled':
            styles=[(replace(s,contour_width=s.major_width,contour_depth=s.major_depth),'major','Contours: Major','#9C4830FF'),
                    (s,'minor','Contours: Minor','#34312FFF')]
    label_zones=[]
    major_claim=None
    for style,kind,part_name,part_color in styles:
        footprint,count=contour_footprint(style,z,w,h,factor,elevation_min,kind,label_zones)
        contour_levels+=count
        if kind=='major':
            major_levels=count
        if footprint is not None and not footprint.is_empty:
            if kind=='minor' and label_zones:
                footprint=footprint.difference(union_all(label_zones))
            if footprint.is_empty:
                continue
            cutter=footprint_prism(footprint,(0,0,w,h),1,ceiling)
            if major_claim is not None:
                cutter=boolean('difference',[cutter,major_claim],allow_empty=True)
            if kind=='major':
                major_claim=cutter
            # GeoPackage features have priority at crossings, for every operation.
            if claimed is not None:
                cutter=boolean('difference',[cutter,claimed],allow_empty=True)
            if not cutter.is_empty:
                if style.contour_operation=='Raised feature':
                    shell=boolean('difference',[solid(z+style.contour_depth,w,h),terrain])
                    contour_part=boolean('intersection',[shell,cutter],allow_empty=True)
                else:
                    cut=style.contour_depth+(style.contour_thickness if style.contour_operation=='Recessed channel' else 0)
                    floor=solid(z-cut,w,h)
                    shell=boolean('difference',[terrain,floor])
                    cavity=boolean('intersection',[shell,cutter,parts[0][2]],allow_empty=True)
                    contour_part=cavity
                    if not cavity.is_empty and cavity.metadata['manifold'].volume()>1e-10:
                        parts[0]=('Terrain','#BCA885FF',boolean('difference',[parts[0][2],cavity]))
                        if style.contour_operation=='Recessed channel':
                            shell=boolean('difference',[solid(z-style.contour_depth,w,h),floor])
                            contour_part=boolean('intersection',[shell,cavity],allow_empty=True)
                if s.contour_operation!='Cut only' and not contour_part.is_empty and contour_part.metadata['manifold'].volume()>1e-10:
                    validate(contour_part)
                    parts.append((part_name,part_color,contour_part))
        if not contour_levels:
            progress("No contour lines cross the sampled terrain at this interval/reference elevation.")
    if aoi is not None:
        progress("Clipping solids to the AOI polygon boundary…")
        clip = footprint_prism(aoi, bounds, factor, ceiling)
        clipped = []
        for name, color, mesh in parts:
            result = boolean("intersection", [mesh, clip], allow_empty=True)
            if not result.is_empty:
                validate(result)
                clipped.append((name, color, result))
        parts = clipped
        if not parts:
            raise ValueError("AOI produced no printable terrain.")
    progress("Validating model…")
    for _, _, mesh in parts:
        validate(mesh)
    minimum = np.min([mesh.bounds[0] for _,_,mesh in parts], axis=0)
    maximum = np.max([mesh.bounds[1] for _,_,mesh in parts], axis=0)
    dimensions = maximum-minimum
    metrics = dict(width=float(dimensions[0]), length=float(dimensions[1]),
                   elevation_min=elevation_min,
                   elevation_max=elevation_min+float(np.ptp(z))/(factor*s.exaggeration),
                   reference_elevation=terrain_reference(s,elevation_min),
                   height=float(dimensions[2]), base=s.base,
                   above_base=float(maximum[2]-s.base),
                   scale=1000/factor, vertical_scale=1000/factor/s.exaggeration)
    if s.contours != 'Off':
        metrics['contour_levels']=contour_levels
        metrics['contour_part']=any(name.startswith('Contours') for name,_,_ in parts)
        metrics['contour_operation']=s.contour_operation
        metrics['major_levels']=major_levels
    if s.nodata_repair!='Off':
        metrics['repair']=repair
    metrics.update(grid_plan(s, metrics))
    if s.export_mode=='Print-bed grid' and (s.tile_connectors!='Off' or s.tile_labels!='Off'):
        from .assembly import prepare_assembly
        parts,assembly=prepare_assembly(parts,metrics,s,progress)
        metrics['assembly']=assembly
    return parts, metrics


def grid_plan(s, metrics):
    if s.export_mode == "Single model":
        return {}
    if s.export_mode != "Print-bed grid":
        raise ValueError("Unknown export mode.")
    for key in ("bed_width", "bed_depth", "print_height"):
        if not math.isfinite(getattr(s,key)) or getattr(s,key) <= 0:
            raise ValueError(f"{key} must be positive and finite.")
    if not math.isfinite(s.bed_margin) or s.bed_margin < 0:
        raise ValueError("Bed edge clearance must be finite and nonnegative.")
    usable_x, usable_y = s.bed_width-2*s.bed_margin, s.bed_depth-2*s.bed_margin
    if min(usable_x,usable_y) <= 0:
        raise ValueError("Bed edge clearance leaves no printable area.")
    if metrics['height'] > s.print_height+1e-6:
        raise ValueError(f"Model height {metrics['height']:.2f} mm exceeds the printer's {s.print_height:.2f} mm limit. "
                         "The grid splits X/Y only; reduce scale, exaggeration or base thickness.")
    cols = max(1,math.ceil(metrics['width']/usable_x-1e-10))
    rows = max(1,math.ceil(metrics['length']/usable_y-1e-10))
    if cols*rows > 400:
        raise ValueError("Grid exceeds 400 cells. Reduce model dimensions or increase the usable bed size.")
    return dict(grid_cols=cols,grid_rows=rows,tile_width=metrics['width']/cols,
                tile_depth=metrics['length']/rows)


def tile_models(parts, plan):
    """Yield occupied cells, keeping all material parts aligned in local XY."""
    minimum = np.min([m.bounds[0] for _,_,m in parts],axis=0)
    maximum = np.max([m.bounds[1] for _,_,m in parts],axis=0)
    dx,dy = plan['tile_width'],plan['tile_depth']
    for row in range(plan['grid_rows']):
        for col in range(plan['grid_cols']):
            x,y = minimum[0]+col*dx, minimum[1]+row*dy
            cutter = trimesh.creation.box(extents=[dx,dy,maximum[2]-minimum[2]+2])
            cutter.apply_translation([x+dx/2,y+dy/2,(minimum[2]+maximum[2])/2])
            clipped = []
            for name,color,mesh in parts:
                # Skip disjoint bounds before the more expensive solid operation.
                if mesh.bounds[1,0] <= x or mesh.bounds[0,0] >= x+dx or mesh.bounds[1,1] <= y or mesh.bounds[0,1] >= y+dy:
                    continue
                piece = boolean("intersection",[mesh,cutter],allow_empty=True)
                if piece.is_empty or piece.metadata['manifold'].volume() <= 1e-10:
                    continue
                validate(piece)
                piece.apply_translation([-x,-y,0])
                piece.metadata.pop('manifold',None)
                clipped.append((name,color,piece))
            if clipped:
                yield row+1,col+1,(float(x),float(y),0),clipped


def write_tiles(s, parts, metrics, progress):
    import json
    import tempfile
    import shutil
    from .three_mf import write_3mf
    output = Path(s.output)
    destination = output.with_name(output.stem+"_tiles")
    if destination.exists():
        raise ValueError(f"Tile folder already exists: {destination}. Choose a new output name to keep the earlier export.")
    plan = grid_plan(s,metrics)
    stage = Path(tempfile.mkdtemp(prefix=".terrain-tiles-",dir=output.parent))
    manifest = dict(unit="millimeter", rows=plan['grid_rows'], columns=plan['grid_cols'],
                    scale=metrics['scale'], vertical_scale=metrics['vertical_scale'],
                    orientation="Rows increase south to north (+Y); columns west to east (+X).",
                    placement="Add assembly_offset_mm to every tile vertex to reassemble the full model.",
                    bed_width=s.bed_width,bed_depth=s.bed_depth,edge_clearance=s.bed_margin,
                    cell_width=plan['tile_width'],cell_depth=plan['tile_depth'],tiles=[])
    try:
        for row,col,offset,tile in tile_models(parts,plan):
            name = f"{output.stem}_r{row:02d}_c{col:02d}.3mf"
            progress(f"Writing tile row {row}, column {col}…")
            write_3mf(stage/name,tile)
            bounds_min=np.min([m.bounds[0] for _,_,m in tile],axis=0)
            bounds_max=np.max([m.bounds[1] for _,_,m in tile],axis=0)
            manifest['tiles'].append(dict(file=name,row=row,column=col,assembly_offset_mm=offset,
                                          dimensions_mm=(bounds_max-bounds_min).tolist(),
                                          materials=[n for n,_,_ in tile]))
        if not manifest['tiles']:
            raise ValueError("Grid produced no printable tiles.")
        if 'assembly' in metrics:
            assembly=metrics['assembly']
            manifest['assembly_details']=assembly
            if assembly['connectors']:
                length,width,depth=assembly['key_dimensions_mm']
                key=trimesh.creation.box(extents=[length,width,depth])
                key.apply_translation([length/2,width/2,depth/2])
                validate(key)
                write_3mf(stage/'alignment-key.3mf',[('Alignment key','#BCA885FF',key)])
                assembly['key_file']='alignment-key.3mf'
                assembly['key_quantity']=len(assembly['connectors'])
        if 'repair' in metrics:
            from .repair import write_repair_report
            write_repair_report(metrics['repair'],stage/'repair-mask')
            manifest['repair_summary']={k:v for k,v in metrics['repair'].items() if k not in ('mask','required')}
        (stage/'layout.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        from .layout_pdf import write_layout_pdf
        progress("Drawing PDF assembly map…")
        write_layout_pdf(manifest,stage/'layout.pdf')
        (stage/'ASSEMBLY.txt').write_text(
            f"Terrain Forge - GIS to 3D Print: {plan['grid_cols']} columns x {plan['grid_rows']} rows\n"
            "View from above with north at the top. Row 01 is at the south/bottom edge; column 01 is at the west/left edge.\n"
            "Missing cells lie outside the AOI. Import each 3MF as one assembly and assign filaments per named part.\n"
            "Tiles retain the original scale and matching cut edges; no seam gaps are added.\n"
            + (f"Print {len(metrics['assembly']['connectors'])} copies of alignment-key.3mf. Insert keys into matching underside sockets; glue if needed. Fit clearance is per side in XY and once in Z.\n" if metrics.get('assembly',{}).get('connectors') else '')
            + (f"Underside markings: {s.tile_labels}. Text is readable from below; arrows point to grid north (+Y). See layout.json for skipped markings.\n" if s.tile_labels!='Off' else '')
            +
            "Use layout.json for exact assembly offsets in millimeters. Tile Z coordinates are preserved.\n",encoding='utf-8')
        stage.rename(destination)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return destination,len(manifest['tiles'])


def generate(s, progress=lambda message: None, prepared=None):
    from .three_mf import write_3mf
    if Path(s.output).suffix.lower() != ".3mf":
        raise ValueError("Output must have a .3mf extension.")
    parts, metrics = prepared if prepared is not None else build_model(s, progress)
    if s.export_mode == "Print-bed grid":
        destination,count = write_tiles(s,parts,metrics,progress)
        location = f"Saved {count} tile assemblies to: {destination}"
    else:
        progress("Writing 3MF assembly…")
        write_3mf(s.output, parts)
        if 'repair' in metrics:
            from .repair import write_repair_report
            write_repair_report(metrics['repair'],Path(s.output).with_suffix('').with_name(Path(s.output).stem+'_repair'))
        location = f"Saved: {s.output}"
    return parts, (f"{metrics['width']:.2f} × {metrics['length']:.2f} × {metrics['height']:.2f} mm; "
                   f"scale 1:{metrics['scale']:.0f}; {len(parts)} material part(s)\n{location}")
