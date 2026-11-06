"""Readable assembly maps for current and earlier layout.json manifests."""
import math
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Flowable


class GridMap(Flowable):
    def __init__(self, tiles, first_row, last_row, first_col, last_col, manifest=None):
        super().__init__()
        self.width, self.height = 750, 325
        self.tiles=tiles
        self.manifest=manifest or {}
        self.r0,self.r1,self.c0,self.c1=first_row,last_row,first_col,last_col

    def draw(self):
        c=self.canv
        columns,rows=self.c1-self.c0+1,self.r1-self.r0+1
        dx,dy=min(690/columns,230),min(275/rows,135)
        left=40+(690-columns*dx)/2
        bottom=20+(275-rows*dy)/2
        for row in range(self.r0,self.r1+1):
            for col in range(self.c0,self.c1+1):
                x,y=left+(col-self.c0)*dx,bottom+(row-self.r0)*dy
                tile=self.tiles.get((row,col))
                c.setFillColor(colors.HexColor('#E5F1EB') if tile else colors.HexColor('#F4F5F6'))
                c.setStrokeColor(colors.HexColor('#8DA39A'))
                c.rect(x,y,dx,dy,fill=1,stroke=1)
                c.setFillColor(colors.HexColor('#183C32') if tile else colors.HexColor('#777777'))
                c.setFont('Helvetica-Bold',11)
                c.drawCentredString(x+dx/2,y+dy/2+7,f'r{row:02d}_c{col:02d}')
                c.setFont('Helvetica',8)
                label='No tile / outside AOI'
                if tile:
                    w,d,h=tile['dimensions_mm']
                    label=f'{w:.1f} x {d:.1f} x {h:.1f} mm'
                c.drawCentredString(x+dx/2,y+dy/2-8,label)
        assembly=self.manifest.get('assembly_details',{})
        origin=assembly.get('model_origin_mm',[0,0])
        cell_w,cell_h=self.manifest.get('cell_width',1),self.manifest.get('cell_depth',1)
        for connector in assembly.get('connectors',[]):
            cx,cy=connector['center_xy_mm']
            gx,gy=(cx-origin[0])/cell_w,(cy-origin[1])/cell_h
            if self.c0-1<=gx<=self.c1 and self.r0-1<=gy<=self.r1:
                x,y=left+(gx-self.c0+1)*dx,bottom+(gy-self.r0+1)*dy
                c.setFillColor(colors.HexColor('#DC7637'))
                c.circle(x,y,3,fill=1,stroke=0)
        c.setFillColor(colors.HexColor('#183C32'))
        c.setFont('Helvetica-Bold',10)
        c.drawString(12,306,'N / +Y  ^')
        c.drawRightString(738,306,'E / +X  >')


def write_layout_pdf(manifest, path):
    rows,cols=manifest.get('rows'),manifest.get('columns')
    if type(rows) is not int or type(cols) is not int or min(rows,cols)<1 or rows*cols>400:
        raise ValueError('Layout must have a grid of 1 to 400 cells.')
    tiles=manifest.get('tiles')
    if not isinstance(tiles,list) or not tiles or len(tiles)>rows*cols:
        raise ValueError('Layout contains no valid tile list.')
    indexed={}
    for tile in tiles:
        row,col=tile.get('row'),tile.get('column')
        dims,offset=tile.get('dimensions_mm'),tile.get('assembly_offset_mm')
        if type(row) is not int or type(col) is not int or not 1<=row<=rows or not 1<=col<=cols or (row,col) in indexed:
            raise ValueError('Layout contains invalid or duplicate tile positions.')
        if not isinstance(tile.get('file'),str) or not tile['file']:
            raise ValueError('Layout contains an invalid tile filename.')
        for values in (dims,offset):
            if not isinstance(values,(list,tuple)) or len(values)!=3 or any(not isinstance(v,(int,float)) or not math.isfinite(v) for v in values):
                raise ValueError('Layout dimensions and offsets must contain three finite numbers.')
        if min(dims)<=0:
            raise ValueError('Tile dimensions must be positive.')
        indexed[row,col]=tile
    if manifest.get('unit')!='millimeter':
        raise ValueError('Layout units must be millimeter.')
    styles=getSampleStyleSheet()
    styles['Title'].textColor=colors.HexColor('#183C32')
    styles['BodyText'].fontSize=9
    styles['BodyText'].leading=12
    styles['Normal'].fontSize=8
    styles['Normal'].leading=10
    doc=SimpleDocTemplate(str(path),pagesize=landscape(A4),leftMargin=36,rightMargin=36,topMargin=28,bottomMargin=32,
                          title='Terrain Forge - GIS to 3D Print - assembly layout',author='Terrain Forge - GIS to 3D Print')
    story=[]
    for top in range(rows,0,-5):
        bottom=max(1,top-4)
        for left in range(1,cols+1,6):
            right=min(cols,left+5)
            if story:
                story.append(PageBreak())
            story.append(Paragraph('Terrain Forge - GIS to 3D Print | Assembly map',styles['Title']))
            story.append(Paragraph(f'<b>{cols} columns x {rows} rows</b> | {len(tiles)} occupied tiles | '
                                   f'This panel: rows {bottom}-{top}, columns {left}-{right}',styles['BodyText']))
            story.append(Spacer(1,8))
            story.append(GridMap(indexed,bottom,top,left,right,manifest))
            story.append(Paragraph('<b>View from above.</b> North is at the top. Row 01 is the bottom (south) row; '
                                   'column 01 is the left (west) column. Green cells contain tiles; gray cells have no tile. '
                                   'Labels match the rXX_cXX suffix of each 3MF filename.',styles['BodyText']))
            story.append(Spacer(1,6))
            story.append(Paragraph('Schematic grid, not a cutting template or exact terrain outline. Dimensions are X x Y x Z in mm. '
                                   'Import each tile as one assembly and assign filament to its named parts. '
                                   'No seam gaps are added. Orange dots, when present, mark underside alignment keys.',styles['BodyText']))
    assembly=manifest.get('assembly_details',{})
    if assembly:
        story.extend([PageBreak(),Paragraph('Keys and underside markings',styles['Title'])])
        keys=assembly.get('connectors',[])
        if keys:
            dims=' x '.join(f'{v:.2f}' for v in assembly['key_dimensions_mm'])
            story.append(Paragraph(f"Print <b>{len(keys)} copies</b> of alignment-key.3mf ({dims} mm). "
                                   f"Fitting clearance: {assembly['clearance_mm']:.2f} mm per XY side and once in Z. "
                                   'Place adjacent tiles together and insert the keys from underneath. Glue may be needed for retention. '
                                   'Test one key/socket fit before printing the full set.',styles['BodyText']))
            story.append(Spacer(1,12))
            connections=[['Key','Adjacent tiles','Axis','Center X / Y (mm)']]
            for key in keys:
                names=' + '.join(f'r{r:02d}_c{c:02d}' for r,c in key['tiles'])
                connections.append([key['id'],names,key['axis'],' / '.join(f'{v:.2f}' for v in key['center_xy_mm'])])
            connection_table=Table(connections,colWidths=[70,250,70,240],repeatRows=1,hAlign='LEFT')
            connection_table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#E5F1EB')),('FONTSIZE',(0,0),(-1,-1),9),
                                                  ('BOTTOMPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),7)]))
            story.append(connection_table)
        else:
            story.append(Paragraph('No alignment keys were placed.',styles['BodyText']))
        story.append(Spacer(1,12))
        story.append(Paragraph(f"Skipped key positions: {assembly.get('skipped_connectors',0)}. "
                               'Positions without enough solid material, roof thickness or clearance are skipped.',styles['BodyText']))
        labels=assembly.get('labels',[])
        placed=sum(label['status']=='placed' for label in labels)
        story.append(Paragraph(f"Underside markings: {escape(assembly.get('label_mode','Off'))}; {placed} placed. "
                               'IDs read from below with grid north upward; arrows point to +Y. '
                               'Embossed badges have recessed backgrounds and retain the original flat bottom plane.',styles['BodyText']))
        skipped=[label['text'] for label in labels if label['status']!='placed']
        if skipped:
            story.append(Paragraph('No safe label area: '+escape(', '.join(skipped)),styles['BodyText']))
    story.extend([PageBreak(),Paragraph('Tile files and assembly positions',styles['Title'])])
    story.append(Paragraph('Add the listed X/Y/Z offset to every vertex of the tile to restore its position in the complete model. '
                           'Offsets describe assembly placement, not slicer bed placement.',styles['BodyText']))
    story.append(Spacer(1,12))
    data=[['Tile','3MF file','Size X/Y/Z (mm)','Assembly X/Y/Z (mm)']]
    for (row,col),tile in sorted(indexed.items()):
        data.append([f'r{row:02d}_c{col:02d}',Paragraph(escape(tile['file']),styles['Normal']),
                     ' / '.join(f'{v:.2f}' for v in tile['dimensions_mm']),
                     ' / '.join(f'{v:.2f}' for v in tile['assembly_offset_mm'])])
    table=Table(data,colWidths=[65,355,160,180],repeatRows=1,hAlign='LEFT')
    table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#183C32')),
                              ('TEXTCOLOR',(0,0),(-1,0),colors.white),('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),
                              ('FONTSIZE',(0,0),(-1,-1),8),('VALIGN',(0,0),(-1,-1),'TOP'),
                              ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#F0F5F2')]),
                              ('BOTTOMPADDING',(0,0),(-1,-1),8),('TOPPADDING',(0,0),(-1,-1),8)]))
    story.append(table)
    def footer(c,doc):
        c.setFont('Helvetica',8)
        c.setFillColor(colors.HexColor('#687A73'))
        c.drawString(36,17,'Terrain Forge - GIS to 3D Print | Assembly guide | Dimensions in millimeters')
        c.drawRightString(805,17,f'Page {doc.page}')
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
