"""Underside assembly details, added without extending tile footprints."""
import math
import numpy as np
from shapely import affinity, union_all
from shapely.geometry import box, Polygon
from .lettering import text_shape


def prepare_assembly(parts,plan,s,progress=lambda text:None):
    from .core import boolean, footprint_prism, validate
    if s.tile_connectors not in ('Off','Underside keys') or s.tile_labels not in ('Off','Engraved','Embossed badge'):
        raise ValueError('Unknown tile connector or label mode.')
    for name in ('key_length','key_width','key_depth','tile_label_height','tile_label_depth'):
        if not math.isfinite(getattr(s,name)) or getattr(s,name)<=0:
            raise ValueError(f'{name} must be positive and finite.')
    if not math.isfinite(s.key_clearance) or s.key_clearance<0:
        raise ValueError('Key clearance must be nonnegative.')
    if min(s.key_length-2*s.key_clearance,s.key_width-2*s.key_clearance,s.key_depth-s.key_clearance)<=0:
        raise ValueError('Key clearance leaves no printable key.')
    usable_x,usable_y=s.bed_width-2*s.bed_margin,s.bed_depth-2*s.bed_margin
    key_x,key_y=s.key_length-2*s.key_clearance,s.key_width-2*s.key_clearance
    if s.tile_connectors!='Off' and not (key_x<=usable_x and key_y<=usable_y or key_y<=usable_x and key_x<=usable_y):
        raise ValueError('The separate alignment key does not fit the usable print bed, even rotated.')
    if s.tile_connectors!='Off' and s.key_depth+.8>=s.base:
        raise ValueError('Base must exceed key socket depth plus 0.8 mm roof thickness.')
    if s.tile_labels!='Off' and s.tile_label_depth+.8>=s.base:
        raise ValueError('Base must exceed label depth plus 0.8 mm roof thickness.')
    terrain=parts[0][2]
    xmin,ymin,_=np.min([m.bounds[0] for _,_,m in parts],axis=0)
    dx,dy=plan['tile_width'],plan['tile_depth']
    rows,cols=plan['grid_rows'],plan['grid_cols']
    cutters=[]
    occupied=[]
    report=dict(connectors=[],labels=[],connector_mode=s.tile_connectors,label_mode=s.tile_labels,
                model_origin_mm=[float(xmin),float(ymin)],
                skipped_connectors=0,key_dimensions_mm=[s.key_length-2*s.key_clearance,s.key_width-2*s.key_clearance,s.key_depth-s.key_clearance],
                socket_dimensions_mm=[s.key_length,s.key_width,s.key_depth],clearance_mm=s.key_clearance)

    def fits(region,depth):
        envelope=footprint_prism(region,(0,0,1,1),1,depth+.8)
        overlap=boolean('intersection',[terrain,envelope],allow_empty=True)
        return abs(overlap.metadata['manifold'].volume()-envelope.volume)<=max(1e-6,envelope.volume*1e-7)

    def cut(region,depth):
        cutter=footprint_prism(region,(0,0,1,1),1,depth+.01)
        cutter.apply_translation([0,0,-.01])
        cutter.metadata.pop('manifold',None)
        cutters.append(cutter)

    if s.tile_connectors!='Off':
        progress('Placing matching underside sockets along internal tile seams…')
        for r in range(1,rows+1):
            for c in range(1,cols+1):
                for axis in ('X','Y'):
                    if axis=='X' and c==cols or axis=='Y' and r==rows:
                        continue
                    for fraction in (1/3,2/3):
                        if axis=='X':
                            x,y=xmin+c*dx,ymin+(r-1+fraction)*dy
                            region=box(x-s.key_length/2,y-s.key_width/2,x+s.key_length/2,y+s.key_width/2)
                            neighbor=[r,c+1]
                            fits_cell=s.key_length/2+1<dx and s.key_width/2+1<min(fraction,1-fraction)*dy
                        else:
                            x,y=xmin+(c-1+fraction)*dx,ymin+r*dy
                            region=box(x-s.key_width/2,y-s.key_length/2,x+s.key_width/2,y+s.key_length/2)
                            neighbor=[r+1,c]
                            fits_cell=s.key_length/2+1<dy and s.key_width/2+1<min(fraction,1-fraction)*dx
                        if not fits_cell or any(region.buffer(.8).intersects(other) for other in occupied) or not fits(region.buffer(.8,join_style=2),s.key_depth):
                            report['skipped_connectors']+=1
                            continue
                        cut(region,s.key_depth)
                        occupied.append(region)
                        report['connectors'].append(dict(id=f"K{len(occupied):03d}",tiles=[[r,c],neighbor],
                                                         axis=axis,center_xy_mm=[float(x),float(y)]))
    if s.tile_labels!='Off':
        progress('Placing underside tile IDs and grid-north arrows…')
        for r in range(1,rows+1):
            for c in range(1,cols+1):
                label=f'R{r:02d} C{c:02d}'
                glyph=text_shape(label,s.tile_label_height)
                gx0,gy0,gx1,gy1=glyph.bounds
                # Mirror X so text is readable from below with grid north upward.
                glyph=affinity.scale(glyph,xfact=-1,yfact=1,origin=((gx0+gx1)/2,0))
                center=(gx0+gx1)/2
                y=gy1+s.tile_label_height*.5
                h=s.tile_label_height
                arrow=Polygon([(center-.12*h,y),(center+.12*h,y),(center+.12*h,y+.8*h),
                               (center+.4*h,y+.8*h),(center,y+1.4*h),(center-.4*h,y+.8*h),(center-.12*h,y+.8*h)])
                glyph=union_all([glyph,arrow])
                gx0,gy0,gx1,gy1=glyph.bounds
                placed=False
                cell=box(xmin+(c-1)*dx+.8,ymin+(r-1)*dy+.8,xmin+c*dx-.8,ymin+r*dy-.8)
                for fx,fy in ((.5,.5),(.5,.25),(.5,.75),(.25,.5),(.75,.5)):
                    x,y=xmin+(c-1+fx)*dx,ymin+(r-1+fy)*dy
                    positioned=affinity.translate(glyph,x-(gx0+gx1)/2,y-(gy0+gy1)/2)
                    badge=box(*positioned.bounds).buffer(.6,join_style=2)
                    if not cell.covers(badge) or any(badge.buffer(.8).intersects(other) for other in occupied) or not fits(badge,s.tile_label_depth):
                        continue
                    region=positioned if s.tile_labels=='Engraved' else badge.difference(positioned)
                    cut(region,s.tile_label_depth)
                    occupied.append(badge)
                    report['labels'].append(dict(row=r,column=c,text=label,status='placed',center_xy_mm=[float(x),float(y)]))
                    placed=True
                    break
                if not placed:
                    report['labels'].append(dict(row=r,column=c,text=label,status='skipped: no safe underside area'))
    if cutters:
        progress('Cutting underside sockets and markings…')
        combined=cutters[0] if len(cutters)==1 else boolean('union',cutters)
        terrain=boolean('difference',[terrain,combined])
        validate(terrain)
        parts=[(parts[0][0],parts[0][1],terrain)]+parts[1:]
    progress(f"Assembly details: {len(report['connectors'])} keys, "
             f"{sum(v['status']=='placed' for v in report['labels'])} marked tiles; "
             f"{report['skipped_connectors']} connector positions skipped.")
    return parts,report
