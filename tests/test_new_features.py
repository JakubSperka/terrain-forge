import json
from pathlib import Path
from dataclasses import replace
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from terrain_forge.core import Settings, solid, build_model, generate, boolean, contour_footprint
from terrain_forge.assembly import prepare_assembly
from terrain_forge.repair import repair_small_holes


def test_selective_repair_keeps_large_and_edge_holes():
    data=np.full((15,15),100.,dtype=float)
    data[4:6,4:6]=np.nan
    data[9:12,9:12]=np.nan
    data[0:2,10]=np.nan
    filled,mask,regions=repair_small_holes(data,np.ones(data.shape,bool),4,5)
    assert mask.sum()==4 and len(regions)==1
    assert filled[4:6,4:6]==pytest.approx(np.full((2,2),100.))
    assert np.isnan(filled[9:12,9:12]).all()
    assert np.isnan(filled[0:2,10]).all()
    assert np.array_equal(data[np.isfinite(data)],filled[np.isfinite(data)])


def test_repair_refuses_outside_aoi_and_unreachable():
    data=np.full((15,15),10.)
    data[5:10,5:10]=np.nan
    required=np.ones(data.shape,bool)
    required[5:10,5:10]=False
    _,mask,_=repair_small_holes(data,required,25,5)
    assert not mask.any()
    _,mask,_=repair_small_holes(data,np.ones(data.shape,bool),25,1)
    assert not mask.any()


@pytest.fixture
def dem(tmp_path):
    path=tmp_path/'dem.tif'
    data=np.tile(np.linspace(100,200,30),(20,1)).astype('float32')
    with rasterio.open(path,'w',driver='GTiff',width=30,height=20,count=1,dtype='float32',
                       crs='EPSG:32633',transform=from_origin(500000,5400000,10,10),nodata=-9999) as dst:
        dst.write(data,1)
    return Settings(str(path),str(tmp_path/'model.3mf'),samples=30)


def test_repair_export_reports_and_source_unchanged(dem):
    with rasterio.open(dem.dem,'r+') as dst:
        data=dst.read(1)
        data[8:11,10:13]=-9999
        dst.write(data,1)
    with pytest.raises(ValueError,match='NoData'):
        build_model(dem)
    dem.nodata_repair='Small enclosed holes'
    dem.repair_max_cells=25
    parts,metrics=build_model(dem)
    assert metrics['repair']['repaired_cells']>0
    generate(dem,prepared=(parts,metrics))
    report=json.loads(Path(dem.output).with_name('model_repair.json').read_text())
    assert report['repaired_cells']==metrics['repair']['repaired_cells']
    with rasterio.open(Path(dem.output).with_name('model_repair.tif')) as src:
        assert int((src.read(1)==1).sum())==report['repaired_cells']
        assert src.crs.to_epsg()==32633
    with rasterio.open(dem.dem) as src:
        assert np.array_equal(src.read(1),data)


def test_major_contour_frequency_negative_levels_and_width():
    s=Settings('','',contour_interval=10,major_every=5,contour_width=2)
    z=np.tile(np.linspace(3,123,121),(11,1))
    major,count=contour_footprint(s,z,120,10,1,-60,kind='major')
    assert count==3  # -50, 0, 50
    minor,minor_count=contour_footprint(replace(s,contour_width=.5),z,120,10,1,-60,kind='minor')
    assert minor_count==8
    assert major.area==pytest.approx(60)
    assert major.intersection(minor).area==0


@pytest.mark.parametrize('operation',['Flush inlay','Recessed channel','Raised feature','Cut only'])
def test_major_minor_materials_all_operations(dem,operation):
    dem.contours='Enabled'
    dem.contour_interval=10
    dem.major_contours='Enabled'
    dem.major_every=5
    dem.contour_operation=operation
    dem.major_labels='Enabled'
    parts,metrics=build_model(dem)
    assert metrics['major_levels']>0
    names=[n for n,_,_ in parts]
    if operation=='Cut only':
        assert names==['Terrain']
    else:
        assert 'Contours: Major' in names and 'Contours: Minor' in names
    assert all(m.is_volume for _,_,m in parts)
    for i in range(len(parts)):
        for j in range(i):
            intersection=boolean('intersection',[parts[i][2],parts[j][2]],allow_empty=True)
            assert abs(intersection.metadata['manifold'].volume())<1e-6


@pytest.mark.parametrize('label_mode',['Engraved','Embossed badge'])
def test_connectors_and_underside_marks(dem,label_mode):
    dem.export_mode='Print-bed grid'
    dem.bed_width,dem.bed_depth=110,80
    dem.tile_connectors='Underside keys'
    dem.tile_labels=label_mode
    parts,metrics=build_model(dem)
    assembly=metrics['assembly']
    assert len(assembly['connectors'])==8
    assert len([x for x in assembly['labels'] if x['status']=='placed'])==4
    assert parts[0][2].is_volume
    assert parts[0][2].bounds[0,2]==pytest.approx(0)
    assert parts[0][2].extents[:2]==pytest.approx([180,120])
    generate(dem,prepared=(parts,metrics))
    folder=Path(dem.output).with_name('model_tiles')
    saved=json.loads((folder/'layout.json').read_text())
    assert saved['assembly_details']['key_quantity']==8
    assert (folder/'alignment-key.3mf').is_file()
    assert len(saved['tiles'])==4
    assert saved['assembly_details']['key_dimensions_mm']==pytest.approx([11.6,3.6,.8])


def test_thin_base_rejected_and_tiny_tiles_skipped():
    s=Settings('','',tile_connectors='Underside keys',base=1)
    parts=[('Terrain','#BCA885FF',solid(np.full((3,3),3.),40,20))]
    plan=dict(tile_width=20,tile_depth=20,grid_cols=2,grid_rows=1)
    with pytest.raises(ValueError,match='roof thickness'):
        prepare_assembly(parts,plan,s)
    s.base=3
    s.tile_labels='Engraved'
    tiny=[('Terrain','#BCA885FF',solid(np.full((3,3),3.),8,4))]
    result,report=prepare_assembly(tiny,dict(tile_width=4,tile_depth=4,grid_cols=2,grid_rows=1),s)
    assert not report['connectors']
    assert report['skipped_connectors']==2
    assert all(label['status'].startswith('skipped') for label in report['labels'])
    assert result[0][2].volume==pytest.approx(tiny[0][2].volume)
