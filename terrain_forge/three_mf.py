"""Portable 3MF Core package with shared-coordinate component objects."""
import os
from pathlib import Path
import tempfile
import zipfile
import xml.etree.ElementTree as ET

NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
ET.register_namespace("", NS)


def write_3mf(path, parts):
    def sub(parent, tag, **attrs):
        return ET.SubElement(parent, f"{{{NS}}}{tag}", {k: str(v) for k,v in attrs.items()})
    root = ET.Element(f"{{{NS}}}model", {"unit": "millimeter", "{http://www.w3.org/XML/1998/namespace}lang": "en-US"})
    sub(root, "metadata", name="Application").text = "Terrain Forge - GIS to 3D Print"
    resources = sub(root, "resources")
    materials = sub(resources, "basematerials", id=1)
    for name, color, _ in parts:
        sub(materials, "base", name=name, displaycolor=color)
    for index, (name, _, mesh) in enumerate(parts):
        obj = sub(resources, "object", id=index+2, type="model", name=name, pid=1, pindex=index)
        element = sub(obj, "mesh")
        vertices = sub(element, "vertices")
        for x,y,z in mesh.vertices:
            sub(vertices, "vertex", x=f"{x:.17g}", y=f"{y:.17g}", z=f"{z:.17g}")
        triangles = sub(element, "triangles")
        for a,b,c in mesh.faces:
            sub(triangles, "triangle", v1=a, v2=b, v3=c)
    assembly_id = len(parts)+2
    assembly = sub(resources, "object", id=assembly_id, type="model", name="Terrain assembly")
    components = sub(assembly, "components")
    for index in range(len(parts)):
        sub(components, "component", objectid=index+2)
    sub(sub(root, "build"), "item", objectid=assembly_id)
    destination = Path(path)
    with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".tmp", delete=False) as tmp:
        temporary = tmp.name
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as package:
            package.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>')
            package.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
            package.writestr("3D/3dmodel.model", ET.tostring(root, encoding="utf-8", xml_declaration=True))
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
