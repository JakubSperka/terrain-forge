import zipfile
import xml.etree.ElementTree as ET
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
import geopandas as gpd
from shapely.geometry import LineString, Polygon
from terrain_forge.core import Settings, generate, solid, load_terrain, boolean, dem_crs, build_model, tile_models, grid_plan
from terrain_forge.three_mf import NS
from terrain_forge.core import contour_footprint, configured_features


@pytest.fixture
def inputs(tmp_path):
    dem = tmp_path / "dem.tif"
    y,x = np.mgrid[0:20,0:30]
    with rasterio.open(dem, "w", driver="GTiff", width=30, height=20, count=1, dtype="float32", crs="EPSG:32633", transform=from_origin(500000,5400000,10,10), nodata=-9999) as dst:
        dst.write((100+x+y*.5).astype("float32"),1)
    gpkg = tmp_path / "features.gpkg"
    gpd.GeoDataFrame(geometry=[LineString([(500020,5399820),(500270,5399980)])], crs="EPSG:32633").to_file(gpkg, layer="river", driver="GPKG")
    return Settings(str(dem),str(tmp_path/"model.3mf"),samples=20,gpkg=str(gpkg),layer="river")


def test_height_field():
    mesh = solid(np.full((4,6),5.),30,20)
    assert mesh.is_volume
    assert mesh.volume == pytest.approx(3000)


@pytest.mark.parametrize("mode", ["Flush inlay","Recessed channel","Raised feature","Cut only"])
def test_pipeline(inputs, mode):
    inputs.mode = mode
    z,w,h,*_ = load_terrain(inputs)
    original = solid(z,w,h)
    parts, _ = generate(inputs)
    assert all(mesh.is_volume for _,_,mesh in parts)
    assert len(parts) == (1 if mode == "Cut only" else 2)
    total = sum(m.volume for _,_,m in parts)
    if mode == "Flush inlay":
        assert total == pytest.approx(original.volume,rel=1e-5)
    elif mode == "Raised feature":
        assert total > original.volume
    else:
        assert total < original.volume
    if len(parts) == 2:
        overlap = boolean("intersection", [p[2] for p in parts],allow_empty=True)
        assert overlap.is_empty or abs(overlap.volume) < 1e-5
    with zipfile.ZipFile(inputs.output) as archive:
        root = ET.fromstring(archive.read("3D/3dmodel.model"))
        assert root.attrib["unit"] == "millimeter"
        assert len(root.findall(f".//{{{NS}}}component")) == len(parts)
        assert len(root.findall(f".//{{{NS}}}base")) == len(parts)
    assert w == pytest.approx(180)
    assert h == pytest.approx(120)


def test_scale_and_exaggeration(inputs):
    inputs.size_mode = "Scale denominator"
    inputs.size = 1000
    z,w,h,*_ = load_terrain(inputs)
    inputs.exaggeration = 3
    z2,*_ = load_terrain(inputs)
    assert (w,h) == pytest.approx((300,200))
    assert np.ptp(z2) == pytest.approx(np.ptp(z)*3)
    assert z2.min() == inputs.base


def test_cut_preserves_floor(inputs):
    inputs.depth = inputs.base
    with pytest.raises(ValueError,match="solid floor"):
        generate(inputs)


def test_polygon_hole(inputs):
    polygon = Polygon([(500030,5399830),(500250,5399830),(500250,5399970),(500030,5399970)], holes=[[(500100,5399870),(500180,5399870),(500180,5399930),(500100,5399930)]])
    gpd.GeoDataFrame(geometry=[polygon],crs="EPSG:32633").to_file(inputs.gpkg,layer="polygon",driver="GPKG")
    inputs.layer = "polygon"
    parts,_ = generate(inputs)
    assert len(parts) == 2
    assert all(m.is_volume for _,_,m in parts)


def test_nodata_rejected(inputs):
    with rasterio.open(inputs.dem,"r+") as dst:
        values = dst.read(1)
        values[5:15,5:15] = -9999
        dst.write(values,1)
    with pytest.raises(ValueError,match="NoData"):
        generate(inputs)


def set_aoi(settings, geometry, geographic=False):
    frame = gpd.GeoDataFrame(geometry=[geometry], crs="EPSG:32633")
    if geographic:
        frame = frame.to_crs("EPSG:4326")
    frame.to_file(settings.gpkg, layer="aoi", driver="GPKG")
    settings.aoi_gpkg = settings.gpkg
    settings.aoi_layer = "aoi"


def test_aoi_exact_boundary_and_hole(inputs):
    from shapely.geometry import box
    aoi = box(500050,5399850,500250,5399950).difference(box(500120,5399880,500180,5399920))
    set_aoi(inputs, aoi)
    with rasterio.open(inputs.dem,"r+") as dst:
        dst.write(np.full((20,30),100,dtype="float32"),1)
    inputs.gpkg = ""
    parts,_ = generate(inputs)
    mesh = parts[0][2]
    assert mesh.is_volume
    assert mesh.extents[:2] == pytest.approx((180,90))
    assert mesh.volume == pytest.approx(aoi.area * (180/200)**2 * inputs.base,rel=1e-6)


@pytest.mark.parametrize("mode", ["Flush inlay","Recessed channel","Raised feature","Cut only"])
def test_aoi_with_features(inputs, mode):
    from shapely.geometry import box
    aoi = box(500050,5399850,500250,5399950)
    set_aoi(inputs,aoi,geographic=True)
    inputs.mode = mode
    parts,_ = generate(inputs)
    assert len(parts) == (1 if mode == "Cut only" else 2)
    for _,_,mesh in parts:
        assert mesh.is_volume
        assert mesh.bounds[0,0] >= -1e-6
        assert mesh.bounds[0,1] >= -1e-6
        assert mesh.bounds[1,0] <= 180+1e-6
        assert mesh.bounds[1,1] <= 90+1e-6
    if len(parts) == 2:
        overlap = boolean("intersection",[p[2] for p in parts],allow_empty=True)
        assert overlap.is_empty or abs(overlap.volume) < 1e-5


def test_aoi_excludes_distant_nodata(inputs):
    from shapely.geometry import box
    set_aoi(inputs,box(500100,5399850,500200,5399950))
    inputs.gpkg = ""
    with rasterio.open(inputs.dem,"r+") as dst:
        data = dst.read(1)
        data[:,:5] = -9999
        dst.write(data,1)
    parts,_ = generate(inputs)
    assert parts[0][2].is_volume


def test_aoi_outside_dem(inputs):
    from shapely.geometry import box
    set_aoi(inputs,box(0,0,10,10))
    with pytest.raises(ValueError,match="overlapping"):
        generate(inputs)


def test_aoi_rejects_lines(inputs):
    inputs.aoi_gpkg = inputs.gpkg
    inputs.aoi_layer = "river"
    with pytest.raises(ValueError,match="polygons only"):
        generate(inputs)


@pytest.mark.parametrize("local_metadata", [False, True])
def test_epsg8353_export(tmp_path, local_metadata):
    crs = 'EPSG:8353'
    if local_metadata:
        crs = 'LOCAL_CS["S-JTSK [JTSK03] / Krovak East North",UNIT["metre",1,AUTHORITY["EPSG","9001"]],AXIS["Easting",EAST],AXIS["Northing",NORTH],AUTHORITY["EPSG","8353"]]'
    dem = tmp_path / 'krovak.tif'
    with rasterio.open(dem,'w',driver='GTiff',width=30,height=20,count=1,
                       dtype='float32',crs=crs,transform=from_origin(-550000,-1240000,10,10)) as dst:
        dst.write(np.full((20,30),200,dtype='float32'),1)
    if local_metadata:
        # GeoTIFF writing strips the incomplete CRS authority; VRT preserves
        # the exact malformed metadata seen in the user's existing dataset.
        from rasterio.shutil import copy as raster_copy
        vrt = tmp_path/'krovak.vrt'
        raster_copy(dem,vrt,driver='VRT')
        tree = ET.parse(vrt)
        tree.getroot().find('SRS').text = crs
        tree.write(vrt,encoding='utf-8')
        dem = vrt
    s = Settings(str(dem),str(tmp_path/'krovak.3mf'),samples=20)
    from shapely.geometry import box
    gpkg = tmp_path/'aoi.gpkg'
    gpd.GeoDataFrame(geometry=[box(-549950,-1240150,-549750,-1240050)],crs='EPSG:8353').to_file(gpkg,layer='aoi',driver='GPKG')
    s.aoi_gpkg, s.aoi_layer = str(gpkg), 'aoi'
    parts,_ = generate(s)
    assert parts[0][2].is_volume
    assert parts[0][2].extents[:2] == pytest.approx((180,90))
    with rasterio.open(dem) as src:
        assert dem_crs(src.crs).to_epsg() == 8353


@pytest.mark.parametrize("crs", [
    'EPSG:4326', 'EPSG:2263',
    'LOCAL_CS["Unknown grid",UNIT["metre",1],AXIS["Easting",EAST],AXIS["Northing",NORTH]]',
    'LOCAL_CS["Conflicting units",UNIT["foot",0.3048],AXIS["Easting",EAST],AXIS["Northing",NORTH],AUTHORITY["EPSG","8353"]]',
])
def test_invalid_dem_crs_still_rejected(crs):
    with pytest.raises(ValueError,match='DEM CRS read as'):
        dem_crs(rasterio.crs.CRS.from_user_input(crs))


@pytest.mark.parametrize("mode", ["Flush inlay", "Recessed channel", "Raised feature", "Cut only"])
def test_precalculated_dimensions_and_export(inputs, mode):
    inputs.mode = mode
    inputs.exaggeration = 2
    prepared = build_model(inputs)
    parts, metrics = prepared
    assert not __import__('pathlib').Path(inputs.output).exists()
    assert metrics['width'] == pytest.approx(180)
    assert metrics['length'] == pytest.approx(120)
    assert metrics['height'] == pytest.approx(max(m.bounds[1,2] for _,_,m in parts))
    assert metrics['base'] + metrics['above_base'] == pytest.approx(metrics['height'])
    assert metrics['vertical_scale'] == pytest.approx(metrics['scale']/2)
    exported, summary = generate(inputs, prepared=prepared)
    assert exported is parts
    assert f"{metrics['height']:.2f}" in summary
    assert __import__('pathlib').Path(inputs.output).is_file()


@pytest.mark.parametrize('mode',['Flush inlay','Recessed channel','Raised feature','Cut only'])
def test_print_bed_grid_preserves_material_volumes(inputs,mode):
    import json
    from pathlib import Path
    inputs.mode=mode
    inputs.export_mode='Print-bed grid'
    inputs.bed_width,inputs.bed_depth,inputs.bed_margin=110,80,5
    parts,metrics=build_model(inputs)
    assert (metrics['grid_cols'],metrics['grid_rows']) == (2,2)
    totals={name:0 for name,_,_ in parts}
    tiles=list(tile_models(parts,metrics))
    assert len(tiles)==4
    for row,col,offset,tile in tiles:
        assert offset[:2] == pytest.approx(((col-1)*90,(row-1)*60))
        for name,_,mesh in tile:
            assert mesh.is_volume
            assert mesh.bounds[0,0] >= -1e-6
            assert mesh.bounds[0,1] >= -1e-6
            assert mesh.bounds[1,0] <= 100+1e-6
            assert mesh.bounds[1,1] <= 70+1e-6
            totals[name]+=mesh.volume
    for name,_,mesh in parts:
        assert totals[name] == pytest.approx(mesh.volume,rel=1e-6)
    generate(inputs,prepared=(parts,metrics))
    folder=Path(inputs.output).with_name('model_tiles')
    layout=json.loads((folder/'layout.json').read_text())
    assert (folder/'layout.pdf').read_bytes().startswith(b'%PDF-')
    assert len(layout['tiles'])==4
    assert not Path(inputs.output).exists()
    import trimesh
    for item in layout['tiles']:
        with zipfile.ZipFile(folder/item['file']) as archive:
            root=ET.fromstring(archive.read('3D/3dmodel.model'))
            meshes=root.findall(f'.//{{{NS}}}mesh')
            assert len(meshes)==len(item['materials'])
            for mesh in meshes:
                vertices=[[float(v.attrib[a]) for a in ('x','y','z')] for v in mesh.findall(f'{{{NS}}}vertices/{{{NS}}}vertex')]
                faces=[[int(f.attrib[a]) for a in ('v1','v2','v3')] for f in mesh.findall(f'{{{NS}}}triangles/{{{NS}}}triangle')]
                assert trimesh.Trimesh(vertices,faces,process=False).is_volume
    with pytest.raises(ValueError,match='already exists'):
        generate(inputs,prepared=(parts,metrics))


def test_grid_skips_empty_aoi_cells():
    import trimesh
    first=trimesh.creation.box(extents=(20,20,3))
    first.apply_translation((10,10,1.5))
    last=first.copy()
    last.apply_translation((80,80,0))
    mesh=trimesh.util.concatenate([first,last])
    parts=[('Terrain','#BCA885FF',mesh)]
    tiles=list(tile_models(parts,dict(grid_rows=2,grid_cols=2,tile_width=50,tile_depth=50)))
    assert [(r,c) for r,c,_,_ in tiles]==[(1,1),(2,2)]
    assert sum(m.volume for _,_,_,tile in tiles for _,_,m in tile)==pytest.approx(mesh.volume)


@pytest.mark.parametrize('margin,height,message',[(60,20,'no printable area'),(5,300,'exceeds')])
def test_grid_rejects_unprintable_limits(margin,height,message):
    s=Settings('','',export_mode='Print-bed grid',bed_width=100,bed_depth=100,bed_margin=margin)
    with pytest.raises(ValueError,match=message):
        grid_plan(s,dict(width=180,length=120,height=height))


def test_contour_position_width_and_exaggeration():
    s=Settings('','',contour_interval=10,contour_width=1)
    z=np.tile(np.linspace(3,23,21),(11,1))
    footprint,count=contour_footprint(s,z,20,10,1,100)
    assert count==1  # 110 m; the boundary extrema 100 and 120 are excluded.
    assert footprint.bounds == pytest.approx((9.5,0,10.5,10))
    assert footprint.area == pytest.approx(10)
    s.exaggeration=3
    exaggerated,_=contour_footprint(s,3+(z-3)*3,20,10,1,100)
    assert exaggerated.symmetric_difference(footprint).area < 1e-8
    s.contour_origin=5
    shifted,count=contour_footprint(s,3+(z-3)*3,20,10,1,100)
    assert count==2
    assert shifted.bounds == pytest.approx((4.5,0,15.5,10))


@pytest.mark.parametrize('mode',['Flush inlay','Recessed channel','Raised feature','Cut only','No features'])
def test_contours_preserve_surface_and_material_separation(inputs,mode):
    if mode=='No features':
        inputs.gpkg=''
    else:
        inputs.mode=mode
    original,_=build_model(inputs)
    inputs.contours='Colored inlays'
    inputs.contour_interval=10
    parts,metrics=build_model(inputs)
    assert metrics['contour_part']
    assert parts[-1][0]=='Contours'
    assert all(m.is_volume for _,_,m in parts)
    assert sum(m.volume for _,_,m in parts)==pytest.approx(sum(m.volume for _,_,m in original),rel=1e-6)
    for i in range(len(parts)):
        for j in range(i):
            overlap=boolean('intersection',[parts[i][2],parts[j][2]],allow_empty=True)
            assert abs(overlap.metadata['manifold'].volume())<1e-6
    assert parts[-1][2].volume < sum(m.volume for _,_,m in original)*0.05


def test_contours_clip_to_aoi_and_export_in_grid(inputs):
    from shapely.geometry import box
    set_aoi(inputs,box(500050,5399850,500250,5399950))
    inputs.contours='Colored inlays'
    inputs.contour_interval=5
    inputs.export_mode='Print-bed grid'
    inputs.bed_width=110
    inputs.bed_depth=70
    parts,metrics=build_model(inputs)
    assert metrics['contour_part']
    for _,_,m in parts:
        assert m.bounds[0,0]>=-1e-6 and m.bounds[1,0]<=180+1e-6
        assert m.bounds[0,1]>=-1e-6 and m.bounds[1,1]<=90+1e-6
    tiles=list(tile_models(parts,metrics))
    contour_volume=sum(m.volume for _,_,_,tile in tiles for name,_,m in tile if name=='Contours')
    assert contour_volume==pytest.approx(parts[-1][2].volume,rel=1e-6)
    generate(inputs,prepared=(parts,metrics))


def test_flat_terrain_no_contours(inputs):
    with rasterio.open(inputs.dem,'r+') as dst:
        dst.write(np.full((20,30),100,dtype='float32'),1)
    inputs.contours='Colored inlays'
    inputs.gpkg=''
    parts,metrics=build_model(inputs)
    assert len(parts)==1
    assert not metrics['contour_part']


@pytest.mark.parametrize('depth,interval,message',[(3,10,'base thickness'),(0.4,0,'positive'),(0.4,0.0001,'500')])
def test_contour_limits(depth,interval,message):
    s=Settings('','',contour_depth=depth,contour_interval=interval)
    with pytest.raises(ValueError,match=message):
        contour_footprint(s,np.array([[3.,23.],[3.,23.]]),20,10,1,100)


@pytest.mark.parametrize('operation',['Flush inlay','Recessed channel','Raised feature','Cut only'])
@pytest.mark.parametrize('features',['Raised feature','Recessed channel'])
def test_contour_operations_with_features(inputs,operation,features):
    inputs.mode=features
    original,_=build_model(inputs)
    original_volume=sum(m.volume for _,_,m in original)
    inputs.contours='Enabled'
    inputs.contour_operation=operation
    inputs.contour_interval=10
    parts,metrics=build_model(inputs)
    volume=sum(m.volume for _,_,m in parts)
    if operation=='Flush inlay':
        assert volume==pytest.approx(original_volume,rel=1e-6)
    elif operation=='Raised feature':
        assert volume>original_volume
    else:
        assert volume<original_volume
    assert metrics['contour_part']==(operation!='Cut only')
    assert all(m.is_volume for _,_,m in parts)
    for i in range(len(parts)):
        for j in range(i):
            overlap=boolean('intersection',[parts[i][2],parts[j][2]],allow_empty=True)
            assert abs(overlap.metadata['manifold'].volume())<1e-6


def test_raised_contours_exceed_base_and_survive_aoi(inputs):
    from shapely.geometry import box
    set_aoi(inputs,box(500050,5399850,500250,5399950))
    inputs.gpkg=''
    inputs.contours='Enabled'
    inputs.contour_operation='Raised feature'
    inputs.contour_depth=10
    inputs.contour_interval=5
    parts,_=build_model(inputs)
    assert parts[-1][0]=='Contours'
    assert parts[-1][2].bounds[1,2]>parts[0][2].bounds[1,2]+5


def test_recessed_contour_floor_limit():
    s=Settings('','',contour_operation='Recessed channel',contour_depth=2,contour_thickness=1)
    with pytest.raises(ValueError,match='base thickness'):
        contour_footprint(s,np.array([[3.,23.],[3.,23.]]),20,10,1,100)


def test_multiple_feature_layers_names_priority_and_export(inputs,tmp_path):
    from dataclasses import asdict
    from terrain_forge.core import FeatureSettings
    roads=tmp_path/'roads.gpkg'
    gpd.GeoDataFrame(geometry=[LineString([(500020,5399980),(500270,5399820)])],crs='EPSG:32633').to_file(roads,layer='roads',driver='GPKG')
    inputs.feature_layers=[asdict(FeatureSettings(inputs.gpkg,'river','Recessed channel',0.5,0.4,2)),
                           asdict(FeatureSettings(str(roads),'roads','Raised feature',1.4,0.4,3))]
    inputs.contours='Enabled'
    inputs.contour_interval=10
    parts,metrics=build_model(inputs)
    names=[name for name,_,_ in parts]
    assert names==['Terrain','Features: river','Features: roads','Contours']
    assert all(mesh.is_volume for _,_,mesh in parts)
    for i in range(len(parts)):
        for j in range(i):
            overlap=boolean('intersection',[parts[i][2],parts[j][2]],allow_empty=True)
            assert abs(overlap.metadata['manifold'].volume())<1e-6
    generate(inputs,prepared=(parts,metrics))
    with zipfile.ZipFile(inputs.output) as archive:
        root=ET.fromstring(archive.read('3D/3dmodel.model'))
        objects=root.findall(f'{{{NS}}}resources/{{{NS}}}object')
        assert [obj.attrib['name'] for obj in objects if obj.find(f'{{{NS}}}mesh') is not None]==names
        assert [m.attrib['name'] for m in root.findall(f'.//{{{NS}}}base')]==names
    # Giving the road priority preserves its full crossing footprint.
    original_road_volume=parts[2][2].volume
    inputs.feature_layers.reverse()
    reordered,_=build_model(inputs)
    assert next(m.volume for n,_,m in reordered if n=='Features: roads')>original_road_volume


def test_duplicate_layer_names_are_distinct(inputs):
    from dataclasses import asdict
    from terrain_forge.core import FeatureSettings
    inputs.feature_layers=[asdict(FeatureSettings(inputs.gpkg,'river',line_width=1)),
                           asdict(FeatureSettings(inputs.gpkg,'river',line_width=3))]
    parts,_=build_model(inputs)
    assert [n for n,_,_ in parts]==['Terrain','Features: river','Features: river (2)']


def test_cut_only_layer_has_priority_and_no_material(inputs):
    from dataclasses import asdict
    from terrain_forge.core import FeatureSettings
    inputs.feature_layers=[asdict(FeatureSettings(inputs.gpkg,'river','Cut only',line_width=3)),
                           asdict(FeatureSettings(inputs.gpkg,'river','Raised feature',line_width=1))]
    parts,_=build_model(inputs)
    assert [n for n,_,_ in parts]==['Terrain']


def test_invalid_multi_layer_settings_rejected(inputs):
    inputs.feature_layers=[dict(gpkg=inputs.gpkg,layer='river',depth=-1)]
    with pytest.raises(ValueError,match='positive'):
        configured_features(inputs)
