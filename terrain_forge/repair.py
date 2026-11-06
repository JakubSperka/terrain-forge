"""Selective repair of enclosed holes in the sampled elevation grid."""
from collections import deque
import numpy as np
from rasterio.fill import fillnodata


def repair_small_holes(data, required, max_cells, radius):
    if type(max_cells) is not int or not 1 <= max_cells <= 10000:
        raise ValueError('Maximum repair hole size must be 1-10000 sampled cells.')
    if type(radius) is not int or not 1 <= radius <= 100:
        raise ValueError('Repair search radius must be 1-100 sampled cells.')
    bad=~np.isfinite(data)
    seen=np.zeros(data.shape,dtype=bool)
    accepted=np.zeros(data.shape,dtype=bool)
    rows,cols=data.shape
    regions=[]
    for start_r,start_c in zip(*np.nonzero(bad)):
        if seen[start_r,start_c]:
            continue
        queue=deque([(start_r,start_c)])
        seen[start_r,start_c]=True
        cells=[]
        enclosed=True
        while queue:
            r,c=queue.popleft()
            cells.append((r,c))
            if r in (0,rows-1) or c in (0,cols-1) or not required[r,c]:
                enclosed=False
            for dr in (-1,0,1):
                for dc in (-1,0,1):
                    rr,cc=r+dr,c+dc
                    if 0<=rr<rows and 0<=cc<cols and bad[rr,cc] and not seen[rr,cc]:
                        seen[rr,cc]=True
                        queue.append((rr,cc))
        if enclosed and len(cells)<=max_cells:
            rr,cc=zip(*cells)
            accepted[rr,cc]=True
            regions.append(dict(cells=len(cells),row_min=int(min(rr)),row_max=int(max(rr)),
                                col_min=int(min(cc)),col_max=int(max(cc))))
    result=data.copy()
    if accepted.any():
        filled=fillnodata(data.copy(),mask=(~bad).astype('uint8'),max_search_distance=radius,smoothing_iterations=0)
        # Unreachable cells remain NaN. Never silently accept a partially filled hole.
        for region in regions[:]:
            r0,r1,c0,c1=region['row_min'],region['row_max'],region['col_min'],region['col_max']
            block=accepted[r0:r1+1,c0:c1+1]
            if not np.isfinite(filled[r0:r1+1,c0:c1+1][block]).all():
                accepted[r0:r1+1,c0:c1+1]=False
                regions.remove(region)
        result[accepted]=filled[accepted]
    return result,accepted,regions


def write_repair_report(report, prefix):
    import json
    from pathlib import Path
    from PIL import Image
    import rasterio
    from rasterio.transform import from_bounds
    prefix=Path(prefix)
    mask,required=report['mask'],report['required']
    classes=np.where(~required,2,np.where(mask,1,0)).astype('uint8')
    west,south,east,north=report['bounds']
    rows,cols=mask.shape
    dx,dy=(east-west)/(cols-1),(north-south)/(rows-1)
    with rasterio.open(str(prefix)+'.tif','w',driver='GTiff',width=cols,height=rows,count=1,
                       dtype='uint8',crs=report['crs'],transform=from_bounds(west-dx/2,south-dy/2,east+dx/2,north+dy/2,cols,rows)) as dst:
        dst.write(classes,1)
        dst.update_tags(classes='0=unchanged;1=repaired;2=outside required AOI interpolation region')
    rgb=np.full((*mask.shape,3),220,dtype=np.uint8)
    rgb[~required]=[55,65,70]
    rgb[mask]=[235,110,35]
    Image.fromarray(rgb).save(str(prefix)+'.png')
    summary={k:v for k,v in report.items() if k not in ('mask','required')}
    summary['legend']='PNG: orange=repaired, light gray=unchanged, dark gray=outside required AOI region. TIFF classes: 0/1/2.'
    Path(str(prefix)+'.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
