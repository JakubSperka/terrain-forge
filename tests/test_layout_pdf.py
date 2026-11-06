import pytest
from terrain_forge.layout_pdf import write_layout_pdf


def manifest(rows=2,cols=3):
    return dict(unit='millimeter',rows=rows,columns=cols,tiles=[
        dict(row=r,column=c,file=f'terrain_r{r:02d}_c{c:02d}.3mf',
             dimensions_mm=[200,150,65],assembly_offset_mm=[(c-1)*200,(r-1)*150,0])
        for r in range(1,rows+1) for c in range(1,cols+1) if (r,c)!=(1,2)])


@pytest.mark.parametrize('rows,cols',[(2,3),(8,9),(1,30)])
def test_layout_pdf_old_manifests_and_large_grids(tmp_path,rows,cols):
    # Earlier JSON exports did not include cell_width/cell_depth.
    path=tmp_path/f'layout-{rows}-{cols}.pdf'
    write_layout_pdf(manifest(rows,cols),path)
    assert path.read_bytes().startswith(b'%PDF-')


def test_invalid_layout_rejected_before_writing(tmp_path):
    data=manifest()
    data['tiles'][0]['column']=400
    path=tmp_path/'bad.pdf'
    with pytest.raises(ValueError,match='positions'):
        write_layout_pdf(data,path)
    assert not path.exists()
