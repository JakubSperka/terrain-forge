"""Simple printable stroke lettering; no installed fonts or GUI required."""
from shapely.geometry import LineString, GeometryCollection
from shapely import union_all, affinity

SEGMENTS={
    'a':[(0,1),(.6,1)],'b':[(.6,1),(.6,.5)],'c':[(.6,.5),(.6,0)],
    'd':[(0,0),(.6,0)],'e':[(0,0),(0,.5)],'f':[(0,.5),(0,1)],'g':[(0,.5),(.6,.5)]}
DIGITS={'0':'abcdef','1':'bc','2':'abged','3':'abgcd','4':'fgbc','5':'afgcd',
        '6':'afgecd','7':'abc','8':'abcdefg','9':'abfgcd','-':'g'}


def text_shape(text,height=3):
    strokes=[]
    for i,char in enumerate(text.upper()):
        paths=[SEGMENTS[k] for k in DIGITS.get(char,'')]
        if char=='R': paths=[[(0,0),(0,1),(.6,1),(.6,.5),(0,.5)],[(.25,.5),(.6,0)]]
        if char=='C': paths=[[(.6,1),(0,1),(0,0),(.6,0)]]
        if char=='N': paths=[[(0,0),(0,1),(.6,0),(.6,1)]]
        if char=='.': paths=[[(.25,0),(.28,0)]]
        for path in paths:
            strokes.append(LineString([(x+i*.85,y) for x,y in path]).buffer(.065,cap_style=2,join_style=2))
    if not strokes:
        return GeometryCollection()
    return affinity.scale(union_all(strokes),height,height,origin=(0,0))
