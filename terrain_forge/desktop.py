import sys
import json
import math
import copy
from dataclasses import asdict
from pathlib import Path
from PySide6.QtCore import QThread, Signal, QSettings, QSignalBlocker, Qt
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QFormLayout,
    QHBoxLayout, QLineEdit, QPushButton, QFileDialog, QComboBox, QDoubleSpinBox,
    QSpinBox, QLabel, QMessageBox, QPlainTextEdit, QScrollArea, QTableWidget,
    QTableWidgetItem, QAbstractItemView, QHeaderView, QDialog, QGroupBox)
import pyogrio
from .core import Settings, generate, build_model, configured_features

FEATURE_KEYS=('gpkg','layer','mode','depth','thickness','line_width')

ICON_PATH = Path(__file__).parent / "assets" / "terrain-forge.png"


class NoWheelMixin:
    def wheelEvent(self, event):
        # Scroll the enclosing form instead of changing even a focused input.
        event.ignore()


class ParameterComboBox(NoWheelMixin, QComboBox):
    pass


class ParameterDoubleSpinBox(NoWheelMixin, QDoubleSpinBox):
    pass


class ParameterSpinBox(NoWheelMixin, QSpinBox):
    pass


PARAMETER_HELP = {
    'nodata_repair':'Opt-in interpolation of small enclosed NoData holes in the sampled grid.\nNever fills edge-connected gaps or edits the source TIFF. Repaired cells are shown in a map and exported as a report.',
    'repair_max_cells':'Maximum number of connected missing cells per hole in the sampled grid, not source raster pixels.\nChanging Samples changes the ground area represented by each cell. Larger or edge-connected holes still stop export.',
    'repair_radius':'Maximum interpolation search radius in sampled cells. A hole is accepted only if every cell can be filled.\nUses inverse-distance interpolation from valid neighbors; no smoothing of existing elevations.',
    'major_contours':'Separate major and minor contours into named material parts. Major contours take priority where bands overlap.\nBoth use the selected contour operation; major width and depth can differ.',
    'major_every':'Every Nth contour relative to the reference elevation becomes a major contour.\nFor interval 20 m and N=5, major levels occur every 100 m, including negative elevations.',
    'major_width':'Printed horizontal width of major contour bands in millimeters.',
    'major_depth':'Major contour inlay depth, channel depth, raised height or groove depth in printed mm.\nThe selected contour operation and channel floor thickness apply to both major and minor contours.',
    'major_labels':'Add elevation numbers in ground meters to major contours using the major contour operation.\nLabels replace a small part of the line, are clipped by the AOI, and yield to GeoPackage features. Very crowded labels may be omitted.',
    'major_label_height':'Printed character height of major elevation labels in millimeters. The value is the DEM elevation after unit conversion.',
    'tile_connectors':'Add matching underside sockets across internal grid seams and export a separate alignment-key.3mf.\nNo protrusions or gaps are added to tile edges. Two candidate key locations per seam; unsafe or narrow locations are skipped and reported.\nRequires Print-bed grid. Keys align adjacent pieces; glue may be needed for retention.',
    'key_length':'Full socket length across the seam in printed mm; half enters each tile. Keys are shortened by twice the clearance.',
    'key_width':'Socket width along the seam in printed mm. Keys are narrowed by twice the clearance.',
    'key_depth':'Socket depth measured up from the underside in mm. A minimum 0.8 mm solid roof is preserved.\nKey thickness equals socket depth minus clearance.',
    'key_clearance':'Fit clearance per side in XY and once in Z, in millimeters.\nFor 0.2 mm clearance, the key is 0.4 mm narrower/shorter and 0.2 mm thinner than its socket. Test fit for your printer.',
    'tile_labels':'Add Rxx Cxx and a grid-north arrow under each tile. Engraved removes the strokes; Embossed badge recesses the background.\nBoth retain a flat bottom plane. Text reads correctly from below with north up. Small/unsafe areas are skipped and reported.',
    'tile_label_height':'Character height of the underside row/column ID in printed mm. Labels are placed away from connectors when possible.',
    'tile_label_depth':'Engraving or badge recess depth in printed mm. At least 0.8 mm of solid roof is preserved.',
    "contours": "Generate thin contour lines directly from the sampled DEM as a separate material named Contours.\n"
                "Choose the contour operation below: inlay, channel, raised line, or empty groove.\n"
                "Assign this part a filament in your slicer. GeoPackage features take priority at crossings.\n"
                "Contours are clipped by the AOI and split with print-bed tiles.",
    "contour_interval": "Vertical spacing between contour elevations in real-world meters, before vertical exaggeration.\n"
                        "For example, 50 gives contours at 0, 50, 100, 150… meters with reference elevation 0.\n"
                        "Contours follow the sampled terrain; increase Samples for finer available DEM detail.",
    "contour_origin": "Reference elevation in real-world meters, using the DEM's vertical datum.\n"
                      "Contour levels equal reference + multiples of interval, including negative multiples.\n"
                      "Use 0 for standard round-number elevations. This is not the model base height.",
    "contour_width": "Full contour-band width in printed millimeters, measured in the horizontal XY plane.\n"
                     "Width stays constant when scale or vertical exaggeration changes. Choose a printable width.\n"
                     "Closely spaced contours can merge; reduce width or increase interval if needed.",
    "contour_depth": "Vertical distance in printed millimeters: inlay thickness, channel recess depth, raised line height, or groove depth.\n"
                     "Independent of the river/feature depth and vertical exaggeration. Cuts must preserve the base floor.",
    "contour_operation": "Flush inlay: colored line flush with the terrain. Recessed channel: lower colored floor.\n"
                          "Raised feature: colored ridge above the terrain. Cut only: empty groove with no colored contour part.\n"
                          "GeoPackage features keep priority where their footprints cross contours.",
    "contour_thickness": "Thickness of the colored floor for recessed contour channels, in printed millimeters.\n"
                          "Total cut = contour depth + floor thickness; it must be less than base thickness. Ignored for other operations.",
    "export_mode": "Single model writes one 3MF. Print-bed grid splits the model into equal X/Y cells\n"
                   "that fit the usable print bed, preserving scale and aligned colored parts.\n"
                   "Writes one 3MF per occupied cell plus assembly instructions into <output name>_tiles.\n"
                   "Optional underside keys are configured in Tile connectors. No gaps or Z slicing are added. Empty AOI cells are skipped.",
    "bed_width": "Printer bed width along X in millimeters. Used only for Print-bed grid.\n"
                 "Usable tile width = bed width minus twice the edge clearance.",
    "bed_depth": "Printer bed depth along Y in millimeters. Used only for Print-bed grid.\n"
                 "Usable tile depth = bed depth minus twice the edge clearance.",
    "bed_margin": "Clearance reserved on every bed edge, in millimeters.\n"
                  "Allow room for brims and printer keep-out zones. This does not remove material between tiles.\n"
                  "Tiles are exported at local XY origin; position the complete assembly on the bed in your slicer.",
    "print_height": "Maximum printable Z height in millimeters. Grid export rejects taller models.\n"
                    "The grid cuts X/Y only. Reduce model size, exaggeration or base thickness if necessary.",
    "dem": "Elevation raster (DEM or DTM) in GeoTIFF format. The first band supplies elevations.\n"
           "Use a north-up raster in a projected CRS with meter units, with no NoData holes.\n"
           "Uses the full raster unless an AOI polygon layer is selected.",
    "aoi_gpkg": "Optional GeoPackage defining the area of interest (AOI). Leave empty for the full DEM.\n"
                "Only the raster region around the AOI is sampled; the model is clipped to its polygon outline.\n"
                "This can be the same GeoPackage as the feature layer, or a different file.",
    "aoi_layer": "Polygon layer defining the model boundary. All polygons in this layer are combined.\n"
                 "Polygon holes are retained; separate polygons produce separate terrain pieces.\n"
                 "The AOI is reprojected to the DEM CRS and clipped to the DEM extent.\n"
                 "Width, height, map scale and sample count use the resulting AOI bounding box.\n"
                 "Elevations must be valid inside and immediately around the boundary for interpolation.",
    "gpkg": "GeoPackage for the selected feature entry. Each entry can use a different file and layer.\n"
            "Add layer copies the current feature settings into the list; edits update the selected entry.\n"
            "The layer must have a CRS; its coordinates are converted to the DEM CRS automatically.",
    "output": "Destination for the .3mf file. Contains terrain and, where applicable, a separate\n"
              "colored feature part in one aligned assembly. Assign filaments in your slicer.\n"
              "You will be asked before replacing an existing file.",
    "layer": "Select a polygon or polyline layer for this feature entry. Multiple entries can be processed together.\n"
             "Polygons keep their outlines and holes. Polylines become bands using Polyline width.\n"
             "Only features overlapping the DEM footprint are included. Ignored without a GeoPackage.",
    "size_mode": "Choose how to size the model horizontally: printed width, printed height, or map scale.\n"
                 "Width runs west to east; height runs south to north (not the vertical Z dimension).\n"
                 "The footprint aspect ratio is preserved. Enter the chosen value below.",
    "size": "For Width or Height, enter the printed dimension in millimeters (for example, 180).\n"
            "For Scale denominator, enter S for a map scale of 1:S (25000 means 1:25,000).\n"
            "At 1:25,000, a 1 km ground distance prints as 40 mm. The other dimension follows automatically.",
    "exaggeration": "Multiplier for terrain relief relative to the horizontal scale.\n"
                    "1 = true proportions; 2 = twice the relief; 0.5 = half the relief.\n"
                    "Does not change the base thickness, feature depth, or raised feature height.",
    "elevation_factor": "Converts the DEM's elevation values to meters before scaling.\n"
                        "Use 1 for elevations in meters, 0.3048 for feet, or 0.01 for centimeters.\n"
                        "This changes elevation units only; the DEM's horizontal CRS must already use meters.",
    "base": "Printed distance in millimeters from the flat model bottom to the lowest sampled terrain point.\n"
            "For flush inlays or empty cuts, this must exceed Cut depth.\n"
            "For recessed channels, it must exceed Cut depth + Colored channel floor thickness.",
    "mode": "Flush inlay: replace a surface layer with colored material, keeping the original surface height.\n"
            "Recessed channel: lower the colored surface by Cut depth, with a solid colored floor below it.\n"
            "Raised feature: add colored material above the terrain, following its relief.\n"
            "Cut only: remove material to create an empty groove, without a second colored part.\n"
            "Applies only when a GeoPackage is selected.",
    "depth": "Vertical distance in printed millimeters, independent of vertical exaggeration.\n"
             "Flush inlay: thickness of the removed material and matching colored insert.\n"
             "Recessed channel: distance from the original terrain to the top of the colored floor.\n"
             "Raised feature: height added above the terrain. Cut only: depth of the empty groove.",
    "thickness": "Thickness of the colored floor in printed millimeters, measured vertically.\n"
                 "Used only for Recessed channel; ignored by the other operations.\n"
                 "The total cut is Cut depth + this thickness. Choose a value suitable for your print layers.",
    "line_width": "Full width of the band around each polyline, in printed millimeters.\n"
                  "For example, 1.2 creates a band extending 0.6 mm to each side of the line.\n"
                  "Applies only to polylines; polygon outlines are unchanged. Choose a printable width.",
    "samples": "Number of elevation samples along the longest edge of the terrain (8–800).\n"
               "The shorter edge gets a proportional number of samples. Default: 160.\n"
               "Higher values capture more available DEM detail but increase processing time and file size.\n"
               "Doubling this value produces roughly four times as many terrain triangles.\n"
               "This does not add detail missing from the original DEM or control vector outline resolution.",
}


class Worker(QThread):
    message = Signal(str)
    success = Signal(str)
    failure = Signal(str)
    calculated = Signal(object)

    def __init__(self, settings, preview=False, prepared=None):
        super().__init__()
        self.settings = settings
        self.preview = preview
        self.prepared = prepared

    def run(self):
        try:
            if self.preview:
                self.calculated.emit(build_model(self.settings, self.message.emit))
            else:
                _, summary = generate(self.settings, self.message.emit, self.prepared)
                self.success.emit(summary)
        except Exception as error:
            self.failure.emit(str(error))


class Window(QWidget):
    def __init__(self, preferences=None):
        super().__init__()
        self.preferences = preferences if preferences is not None else QSettings("TerrainForge", "TerrainForge")
        self.prepared = None
        self.prepared_settings = None
        self.prepared_files = None
        self.feature_layers=[]
        self._syncing_features=False
        self.setWindowTitle("Terrain Forge - GIS to 3D Print")
        self.setWindowIcon(QIcon(str(ICON_PATH)))
        available = self.screen().availableGeometry()
        self.resize(min(1100, available.width()-40), min(1150, available.height()-40))
        layout = QVBoxLayout(self)
        title = QLabel("Terrain Forge - GIS to 3D Print")
        title.setStyleSheet("font-size: 26px; font-weight: bold")
        header = QHBoxLayout()
        mark = QLabel()
        mark.setPixmap(self.windowIcon().pixmap(48,48))
        header.addWidget(mark)
        header.addWidget(title,1)
        layout.addLayout(header)
        note = QLabel("Create a solid terrain model with aligned colored features.\nDEM: projected CRS in meters. Feature dimensions: printed millimeters.")
        layout.addWidget(note)
        self.form_widget = QWidget()
        sections_layout = QVBoxLayout(self.form_widget)
        sections_layout.setSpacing(16)
        self.form_widget.setStyleSheet('''
            QGroupBox { border: 1px solid palette(mid); border-radius: 6px;
                        margin-top: 12px; padding: 14px 10px 10px 10px; }
            QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left;
                               left: 12px; padding: 0 6px; font-weight: bold; }
        ''')
        section_specs = [
            ('Input data & area of interest', ('dem','aoi_gpkg','aoi_layer')),
            ('NoData repair', ('nodata_repair','repair_max_cells','repair_radius')),
            ('Model dimensions & terrain', ('size_mode','size','exaggeration','elevation_factor','base','samples')),
            ('Feature layers & operations', ('gpkg','layer','mode','depth','thickness','line_width')),
            ('Contour lines', ('contours','contour_operation','contour_interval','contour_origin','contour_width','contour_depth','contour_thickness')),
            ('Major contours & elevation labels', ('major_contours','major_labels','major_every','major_width','major_depth','major_label_height')),
            ('Print-bed grid', ('export_mode','bed_width','bed_depth','bed_margin','print_height')),
            ('Tile connectors / alignment keys', ('tile_connectors','key_length','key_width','key_depth','key_clearance')),
            ('Underside tile IDs & north arrows', ('tile_labels','tile_label_height','tile_label_depth')),
            ('Output file', ('output',)),
        ]
        parameter_forms = {}
        self.parameter_sections = []
        for heading, keys in section_specs:
            group = QGroupBox(heading.replace('&', '&&'))
            group_form = QFormLayout(group)
            group_form.setVerticalSpacing(8)
            group_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            sections_layout.addWidget(group)
            self.parameter_sections.append(group)
            for key in keys:
                parameter_forms[key] = group_form
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.form_widget)
        layout.addWidget(scroll, 1)
        self.fields = {}

        def add_parameter(label, control, key):
            form = parameter_forms[key]
            form.addRow(label, control)
            tooltip = PARAMETER_HELP[key]
            self.fields[key].setToolTip(tooltip)
            self.fields[key].setToolTipDuration(30000)
            form.labelForField(control).setToolTip(tooltip)
            form.labelForField(control).setToolTipDuration(30000)

        for key, label, save in [("dem", "Elevation GeoTIFF", False), ("aoi_gpkg", "AOI GeoPackage (optional)", False), ("gpkg", "Feature GeoPackage (optional)", False), ("output", "Output 3MF", True)]:
            row = QHBoxLayout()
            entry = QLineEdit()
            self.fields[key] = entry
            button = QPushButton("Browse…")
            button.setToolTip(PARAMETER_HELP[key])
            button.setToolTipDuration(30000)
            button.clicked.connect(lambda checked=False, k=key, s=save: self.browse(k,s))
            row.addWidget(entry)
            row.addWidget(button)
            add_parameter(label, row, key)
        self.fields["gpkg"].editingFinished.connect(lambda: self.layers("gpkg"))
        self.fields["aoi_gpkg"].editingFinished.connect(lambda: self.layers("aoi_gpkg"))
        self.fields["aoi_layer"] = ParameterComboBox()
        add_parameter("AOI polygon layer", self.fields["aoi_layer"], "aoi_layer")
        self.fields["layer"] = ParameterComboBox()
        add_parameter("Vector layer", self.fields["layer"], "layer")
        for key,label,items in [("size_mode", "Sizing", ["Width (mm)", "Height (mm)", "Scale denominator"]), ("mode", "Feature operation", ["Flush inlay", "Recessed channel", "Raised feature", "Cut only"])]:
            field = ParameterComboBox()
            field.addItems(items)
            self.fields[key] = field
            add_parameter(label, field, key)
        for key,label,value,maximum in [
            ("size", "Width / height / scale denominator", 180, 1000000000),
            ("exaggeration", "Vertical exaggeration (×)", 1, 1000),
            ("elevation_factor", "Elevation units to meters (feet = 0.3048)", 1, 1000),
            ("base", "Base thickness (mm)", 3, 1000),
            ("depth", "Cut depth / raised height (mm)", 0.8, 1000),
            ("thickness", "Colored channel floor thickness (mm)", 0.6, 1000),
            ("line_width", "Polyline width (mm)", 1.2, 1000)]:
            field = ParameterDoubleSpinBox()
            field.setDecimals(4)
            field.setRange(0.0001, maximum)
            field.setValue(value)
            self.fields[key] = field
            add_parameter(label, field, key)
        self.feature_table=QTableWidget(0,6)
        self.feature_table.setHorizontalHeaderLabels(['Layer','Operation','Depth / height','Floor','Line width','GeoPackage'])
        self.feature_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.feature_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.feature_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.feature_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.feature_table.horizontalHeader().setSectionResizeMode(5,QHeaderView.ResizeMode.Stretch)
        self.feature_table.setMinimumHeight(150)
        self.feature_table.setMaximumHeight(200)
        self.feature_table.setToolTip('Top row has highest priority at overlaps. Each row becomes a separately named Features part, except Cut only.\nSelect a row to edit it using the file, layer and feature controls above. Changes apply immediately.\nWith no rows, the single feature settings above are used for backward compatibility.')
        parameter_forms['gpkg'].addRow('Feature layers (top wins)',self.feature_table)
        buttons=QHBoxLayout()
        for text,callback,help_text in [
            ('Add layer',self.add_feature,'Add a copy of the current feature settings, then edit the new selected row.'),
            ('Remove',self.remove_feature,'Remove the selected feature entry.'),
            ('Move up',lambda:self.move_feature(-1),'Increase priority at overlapping footprints.'),
            ('Move down',lambda:self.move_feature(1),'Decrease priority at overlapping footprints.')]:
            button=QPushButton(text)
            button.clicked.connect(callback)
            button.setToolTip(help_text)
            buttons.addWidget(button)
        parameter_forms['gpkg'].addRow('',buttons)
        resolution = ParameterSpinBox()
        resolution.setRange(8,800)
        resolution.setValue(160)
        self.fields["samples"] = resolution
        add_parameter("Samples on longest edge", resolution, "samples")
        self.fields["export_mode"] = ParameterComboBox()
        self.fields["export_mode"].addItems(["Single model", "Print-bed grid"])
        add_parameter("Export layout", self.fields["export_mode"], "export_mode")
        for key,label,default in [("bed_width","Print bed width X (mm)",220),
                                  ("bed_depth","Print bed depth Y (mm)",220),
                                  ("bed_margin","Bed edge clearance (mm)",5),
                                  ("print_height","Maximum print height Z (mm)",250)]:
            field=ParameterDoubleSpinBox()
            field.setDecimals(2)
            field.setRange(0 if key == "bed_margin" else 0.01,10000)
            field.setValue(default)
            self.fields[key]=field
            add_parameter(label,field,key)
            field.setEnabled(False)
        self.fields["export_mode"].currentTextChanged.connect(self.update_grid_controls)
        self.fields['contours']=ParameterComboBox()
        self.fields['contours'].addItems(['Off','Enabled'])
        add_parameter('Contour lines',self.fields['contours'],'contours')
        self.fields['contour_operation']=ParameterComboBox()
        self.fields['contour_operation'].addItems(['Flush inlay','Recessed channel','Raised feature','Cut only'])
        add_parameter('Contour operation',self.fields['contour_operation'],'contour_operation')
        self.fields['contour_operation'].setEnabled(False)
        for key,label,default in [('contour_interval','Contour elevation interval (m)',50),
                                  ('contour_origin','Contour reference elevation (m)',0),
                                  ('contour_width','Contour printed width (mm)',0.6),
                                  ('contour_depth','Contour depth / raised height (mm)',0.4),
                                  ('contour_thickness','Contour channel floor thickness (mm)',0.4)]:
            field=ParameterDoubleSpinBox()
            field.setDecimals(4)
            field.setRange(-100000 if key=='contour_origin' else 0.0001,100000)
            field.setValue(default)
            self.fields[key]=field
            add_parameter(label,field,key)
            field.setEnabled(False)
        self.fields['contours'].currentTextChanged.connect(self.update_contour_controls)
        self.fields['contour_operation'].currentTextChanged.connect(lambda: self.update_contour_controls(self.fields['contours'].currentText()))
        for key,label,items in [
            ('major_contours','Major / minor contours',['Off','Enabled']),
            ('major_labels','Major elevation labels',['Off','Enabled']),
            ('tile_connectors','Tile connectors',['Off','Underside keys']),
            ('tile_labels','Underside tile IDs',['Off','Engraved','Embossed badge']),
            ('nodata_repair','Repair small NoData holes',['Off','Small enclosed holes'])]:
            widget=ParameterComboBox()
            widget.addItems(items)
            self.fields[key]=widget
            add_parameter(label,widget,key)
        for key,label,default,minimum,maximum,integer in [
            ('major_every','Major contour every N lines',5,2,100,True),
            ('major_width','Major contour width (mm)',1,.01,100,False),
            ('major_depth','Major contour depth / height (mm)',.6,.01,100,False),
            ('major_label_height','Elevation label height (mm)',2.5,.5,30,False),
            ('key_length','Key socket length (mm)',12,1,100,False),
            ('key_width','Key socket width (mm)',4,1,100,False),
            ('key_depth','Key socket depth (mm)',1,.1,30,False),
            ('key_clearance','Key fitting clearance (mm)',.2,0,5,False),
            ('tile_label_height','Tile ID character height (mm)',3,1,30,False),
            ('tile_label_depth','Tile ID engraving / badge depth (mm)',.4,.1,10,False),
            ('repair_max_cells','Maximum hole area (sampled cells)',16,1,10000,True),
            ('repair_radius','Repair search radius (sampled cells)',5,1,100,True)]:
            widget=ParameterSpinBox() if integer else ParameterDoubleSpinBox()
            if not integer:
                widget.setDecimals(3)
            widget.setRange(minimum,maximum)
            widget.setValue(default)
            self.fields[key]=widget
            add_parameter(label,widget,key)
        for key in ('major_contours','major_labels','contours','export_mode','tile_connectors','tile_labels','nodata_repair'):
            self.fields[key].currentTextChanged.connect(self.update_extra_controls)
        self.update_extra_controls()
        # Match keyboard navigation to the visual section order.
        ordered_fields = [self.fields[key] for _, keys in section_specs for key in keys]
        for previous, following in zip(ordered_fields, ordered_fields[1:]):
            QWidget.setTabOrder(previous, following)
        settings_row = QHBoxLayout()
        self.save_button = QPushButton("Save settings…")
        self.load_button = QPushButton("Load settings…")
        self.save_button.setToolTip("Save all input files, selected layers and parameters to a reusable JSON settings file.")
        self.load_button.setToolTip("Restore input files, layers and parameters from a saved JSON settings file.")
        self.save_button.clicked.connect(self.save_settings)
        self.load_button.clicked.connect(self.load_settings)
        settings_row.addWidget(self.save_button)
        settings_row.addWidget(self.load_button)
        layout.addLayout(settings_row)
        self.layout_pdf_button=QPushButton('Layout JSON to PDF…')
        self.layout_pdf_button.setToolTip('Open an existing layout.json and save a graphical PDF assembly map.\nNew grid exports already include layout.pdf automatically.')
        self.layout_pdf_button.clicked.connect(self.convert_layout_pdf)
        layout.addWidget(self.layout_pdf_button)
        self.calculate = QPushButton("Calculate dimensions && scale")
        self.calculate.setToolTip("Calculate the final model bounds, including AOI clipping and feature operations, without writing a 3MF.\nLarge models may take time. The calculated model is reused on export if settings stay unchanged.")
        self.calculate.clicked.connect(self.run_calculation)
        layout.addWidget(self.calculate)
        self.repair_map_button=QPushButton('Show NoData repair map…')
        self.repair_map_button.setEnabled(False)
        self.repair_map_button.clicked.connect(self.show_repair_map)
        layout.addWidget(self.repair_map_button)
        self.dimensions = QLabel("Dimensions not calculated. Choose inputs, then click Calculate dimensions & scale.")
        self.dimensions.setWordWrap(True)
        self.dimensions.setToolTip("Z height includes the base and all raised features. Above-base height includes terrain and features.\nHorizontal scale is ground distance / printed distance. Vertical scale also accounts for exaggeration.\nChanging any model setting invalidates this calculation; changing the output path does not.")
        layout.addWidget(self.dimensions)
        self.export = QPushButton("Generate 3MF")
        self.export.setToolTip("Build and validate the terrain and feature solids, then save the 3MF assembly.\nProgress and any errors appear below. Detailed models may take some time.")
        self.export.clicked.connect(self.run_export)
        layout.addWidget(self.export)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(140)
        layout.addWidget(self.log)
        self.worker = None
        for key, field in self.fields.items():
            if key == "output":
                continue
            signal = field.textChanged if isinstance(field, QLineEdit) else field.currentTextChanged if isinstance(field, QComboBox) else field.valueChanged
            signal.connect(self.invalidate_calculation)
            if key in FEATURE_KEYS:
                signal.connect(self.sync_selected_feature)
        self.feature_table.itemSelectionChanged.connect(self.select_feature)

    def feature_values(self):
        return {key:(self.fields[key].text().strip() if isinstance(self.fields[key],QLineEdit)
                     else self.fields[key].currentText() if isinstance(self.fields[key],QComboBox)
                     else self.fields[key].value()) for key in FEATURE_KEYS}

    def refresh_feature_table(self, selected=-1):
        blocker=QSignalBlocker(self.feature_table)
        self.feature_table.setRowCount(len(self.feature_layers))
        for row,entry in enumerate(self.feature_layers):
            cells=[entry['layer'],entry['mode'],f"{entry['depth']:g}",f"{entry['thickness']:g}",
                   f"{entry['line_width']:g}",Path(entry['gpkg']).name]
            for col,text in enumerate(cells):
                item=QTableWidgetItem(text)
                item.setToolTip(entry['gpkg'] if col==5 else text)
                self.feature_table.setItem(row,col,item)
        self.feature_table.clearSelection()
        self.feature_table.setCurrentCell(-1,-1)
        if 0<=selected<len(self.feature_layers):
            self.feature_table.selectRow(selected)
        del blocker

    def add_feature(self):
        entry=self.feature_values()
        try:
            configured_features(Settings('','',base=self.fields['base'].value(),feature_layers=[entry]))
            if len(self.feature_layers)>=30:
                raise ValueError('Use at most 30 feature layers.')
        except ValueError as error:
            QMessageBox.warning(self,'Cannot add layer',str(error))
            return
        self.feature_layers.append(entry)
        self.refresh_feature_table(len(self.feature_layers)-1)
        self.invalidate_calculation()

    def sync_selected_feature(self,*args):
        row=self.feature_table.currentRow()
        if not self._syncing_features and 0<=row<len(self.feature_layers):
            self.feature_layers[row]=self.feature_values()
            self.refresh_feature_table(row)

    def select_feature(self):
        row=self.feature_table.currentRow()
        if self._syncing_features or not 0<=row<len(self.feature_layers):
            return
        entry=self.feature_layers[row]
        self._syncing_features=True
        try:
            self.fields['gpkg'].setText(entry['gpkg'])
            self.layers('gpkg')
            for key,value in entry.items():
                field=self.fields[key]
                if isinstance(field,QComboBox):
                    field.setCurrentIndex(field.findText(value))
                elif not isinstance(field,QLineEdit):
                    field.setValue(value)
        finally:
            self._syncing_features=False

    def remove_feature(self):
        row=self.feature_table.currentRow()
        if not 0<=row<len(self.feature_layers):
            return
        self.feature_layers.pop(row)
        self.refresh_feature_table(min(row,len(self.feature_layers)-1))
        if self.feature_layers:
            self.select_feature()
        else:
            self.fields['gpkg'].clear()
            self.fields['layer'].clear()
        self.invalidate_calculation()

    def move_feature(self,step):
        row=self.feature_table.currentRow()
        target=row+step
        if 0<=row<len(self.feature_layers) and 0<=target<len(self.feature_layers):
            self.feature_layers[row],self.feature_layers[target]=self.feature_layers[target],self.feature_layers[row]
            self.refresh_feature_table(target)
            self.invalidate_calculation()

    def invalidate_calculation(self, *args):
        self.prepared = self.prepared_settings = None
        self.prepared_files = None
        self.repair_map_button.setEnabled(False)
        self.dimensions.setText("Dimensions not current — click Calculate dimensions & scale.")

    def update_grid_controls(self, mode):
        for key in ("bed_width","bed_depth","bed_margin","print_height"):
            self.fields[key].setEnabled(mode == "Print-bed grid")

    def update_contour_controls(self, mode):
        for key in ('contour_interval','contour_origin','contour_width','contour_depth','contour_operation'):
            self.fields[key].setEnabled(mode != 'Off')
        self.fields['contour_thickness'].setEnabled(mode != 'Off' and self.fields['contour_operation'].currentText()=='Recessed channel')

    def update_extra_controls(self,*args):
        major=self.fields['contours'].currentText()!='Off' and self.fields['major_contours'].currentText()=='Enabled'
        self.fields['major_contours'].setEnabled(self.fields['contours'].currentText()!='Off')
        for key in ('major_every','major_width','major_depth','major_labels'):
            self.fields[key].setEnabled(major)
        self.fields['major_label_height'].setEnabled(major and self.fields['major_labels'].currentText()=='Enabled')
        grid=self.fields['export_mode'].currentText()=='Print-bed grid'
        for key in ('tile_connectors','tile_labels'):
            self.fields[key].setEnabled(grid)
        for key in ('key_length','key_width','key_depth','key_clearance'):
            self.fields[key].setEnabled(grid and self.fields['tile_connectors'].currentText()!='Off')
        for key in ('tile_label_height','tile_label_depth'):
            self.fields[key].setEnabled(grid and self.fields['tile_labels'].currentText()!='Off')
        for key in ('repair_max_cells','repair_radius'):
            self.fields[key].setEnabled(self.fields['nodata_repair'].currentText()!='Off')

    def show_repair_map(self):
        if self.prepared is None or 'repair' not in self.prepared[1]:
            return
        import numpy as np
        report=self.prepared[1]['repair']
        mask,required=report['mask'],report['required']
        pixels=np.full((*mask.shape,3),220,dtype=np.uint8)
        pixels[~required]=[55,65,70]
        pixels[mask]=[235,110,35]
        image=QImage(pixels.data,pixels.shape[1],pixels.shape[0],pixels.strides[0],QImage.Format.Format_RGB888).copy()
        dialog=QDialog(self)
        dialog.setWindowTitle('NoData repair map - sampled grid, north up')
        layout=QVBoxLayout(dialog)
        layout.addWidget(QLabel(f"{report['repaired_cells']} repaired cells in {len(report['regions'])} holes. Orange: repaired; gray: unchanged; dark: outside AOI.\nSource DEM is unchanged. The export includes PNG, georeferenced TIFF mask, and JSON repair details."))
        picture=QLabel()
        picture.setPixmap(QPixmap.fromImage(image).scaled(900,650,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.FastTransformation))
        layout.addWidget(picture)
        dialog.exec()

    def convert_layout_pdf(self):
        source,_=QFileDialog.getOpenFileName(self,'Open tile layout JSON',self.dialog_start(),'Tile layout (*.json)')
        if not source:
            return
        destination,_=QFileDialog.getSaveFileName(self,'Save assembly map',str(Path(source).with_suffix('.pdf')),'PDF (*.pdf)')
        if not destination:
            return
        try:
            from .layout_pdf import write_layout_pdf
            if not Path(destination).suffix:
                destination+='.pdf'
            if Path(source).resolve()==Path(destination).resolve():
                raise ValueError('Choose a different output file to preserve the JSON layout.')
            write_layout_pdf(json.loads(Path(source).read_text(encoding='utf-8')),destination)
            self.remember_directory(destination)
            self.log.appendPlainText('PDF assembly map saved: '+destination)
        except Exception as error:
            QMessageBox.warning(self,'Cannot create layout PDF',str(error))

    def dialog_start(self, current=""):
        if current and Path(current).parent.is_dir():
            return current
        remembered = str(self.preferences.value("last_directory", ""))
        return remembered if remembered and Path(remembered).is_dir() else str(Path.home())

    def remember_directory(self, path):
        self.preferences.setValue("last_directory", str(Path(path).resolve().parent))
        self.preferences.sync()

    def browse(self, key, save):
        filters = {"dem": "GeoTIFF (*.tif *.tiff)", "gpkg": "GeoPackage (*.gpkg)", "aoi_gpkg": "GeoPackage (*.gpkg)", "output": "3MF (*.3mf)"}
        method = QFileDialog.getSaveFileName if save else QFileDialog.getOpenFileName
        path, _ = method(self, "Select file", self.dialog_start(self.fields[key].text()), filters[key])
        if path:
            self.remember_directory(path)
            self.fields[key].setText(path)
            if key in ("gpkg", "aoi_gpkg"):
                self.layers(key)
            if key == "dem" and not self.fields["output"].text():
                self.fields["output"].setText(str(Path(path).with_suffix(".3mf")))

    def layers(self, key="gpkg"):
        target = self.fields["aoi_layer" if key == "aoi_gpkg" else "layer"]
        previous = target.currentText()
        target.clear()
        path = self.fields[key].text().strip()
        if path:
            try:
                target.addItems([str(name) for name,kind in pyogrio.list_layers(path)
                                 if kind and (key != "aoi_gpkg" or "polygon" in kind.lower())])
                if previous and target.findText(previous) >= 0:
                    target.setCurrentText(previous)
                if key == "aoi_gpkg" and target.count() == 0:
                    QMessageBox.warning(self, "No AOI polygons", "This GeoPackage contains no polygon layers.")
            except Exception as error:
                QMessageBox.warning(self, "Cannot read GeoPackage", str(error))

    def values(self):
        values = {}
        for key,field in self.fields.items():
            if isinstance(field, QLineEdit):
                values[key] = field.text().strip()
            elif isinstance(field, QComboBox):
                values[key] = field.currentText()
            else:
                values[key] = field.value()
        values['feature_layers']=copy.deepcopy(self.feature_layers)
        return values

    def save_settings(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save settings", self.dialog_start(), "Terrain Forge settings (*.json)")
        if not path:
            return
        try:
            if not Path(path).suffix:
                path += ".json"
            values = self.values()
            for key in ("dem", "gpkg", "aoi_gpkg", "output"):
                if values[key]:
                    values[key] = str(Path(values[key]).resolve())
            for entry in values['feature_layers']:
                entry['gpkg']=str(Path(entry['gpkg']).resolve())
            Path(path).write_text(json.dumps({"version": 1, "settings": values}, indent=2), encoding="utf-8")
            self.remember_directory(path)
            self.log.appendPlainText("Settings saved: " + path)
        except Exception as error:
            QMessageBox.warning(self, "Cannot save settings", str(error))

    def load_settings(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load settings", self.dialog_start(), "Terrain Forge settings (*.json)")
        if not path:
            return
        try:
            document = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(document, dict) or document.get("version") != 1 or not isinstance(document.get("settings"), dict):
                raise ValueError("Unsupported settings file. Expected Terrain Forge settings version 1.")
            values = asdict(Settings("", ""))
            if document["settings"].keys() - values.keys():
                raise ValueError("Settings file contains unknown parameters.")
            values.update(document["settings"])
            if values['contours']=='Colored inlays':
                values['contours']='Enabled'
            # Validate everything before modifying the current form.
            configured_features(Settings(**values))
            for key, value in values.items():
                if key=='feature_layers':
                    continue
                field = self.fields[key]
                if isinstance(field, (QLineEdit, QComboBox)):
                    if not isinstance(value, str):
                        raise ValueError(f"{key} must be text.")
                    if key not in ('layer','aoi_layer') and isinstance(field,QComboBox) and field.findText(value) < 0:
                        raise ValueError(f"Unknown {key}: {value}")
                else:
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not field.minimum() <= value <= field.maximum():
                        raise ValueError(f"{key} is outside its allowed range.")
                    if isinstance(field,QSpinBox) and int(value) != value:
                        raise ValueError("Samples must be a whole number.")
            for key in ("dem", "gpkg", "aoi_gpkg", "output"):
                if values[key] and not Path(values[key]).is_absolute():
                    values[key] = str((Path(path).parent / values[key]).resolve())
            for entry in values['feature_layers']:
                if not Path(entry['gpkg']).is_absolute():
                    entry['gpkg']=str((Path(path).parent/entry['gpkg']).resolve())
            self._syncing_features=True
            for key, value in values.items():
                if key=='feature_layers':
                    continue
                field = self.fields[key]
                if isinstance(field, QLineEdit):
                    field.setText(value)
                elif isinstance(field, QComboBox):
                    if key not in ("layer", "aoi_layer"):
                        field.setCurrentText(value)
                else:
                    field.setValue(int(value) if isinstance(field,QSpinBox) else value)
            for key, layer_key in (("gpkg", "layer"), ("aoi_gpkg", "aoi_layer")):
                self.layers(key)
                field = self.fields[layer_key]
                field.setCurrentIndex(field.findText(values[layer_key]))
                if values[key] and field.currentIndex() < 0:
                    self.log.appendPlainText(f"Select a valid layer for {key}; saved layer was not found.")
            self.feature_layers=copy.deepcopy(values['feature_layers'])
            self.refresh_feature_table()
            self._syncing_features=False
            self.invalidate_calculation()
            self.remember_directory(path)
            self.log.appendPlainText("Settings loaded: " + path)
        except Exception as error:
            self._syncing_features=False
            QMessageBox.warning(self, "Cannot load settings", str(error))

    def set_busy(self, busy):
        for control in (self.export, self.calculate, self.form_widget, self.save_button, self.load_button, self.layout_pdf_button):
            control.setEnabled(not busy)
        self.repair_map_button.setEnabled(not busy and self.prepared is not None and 'repair' in self.prepared[1])

    def run_calculation(self):
        values = self.values()
        if not values["dem"]:
            QMessageBox.warning(self, "Missing DEM", "Choose an elevation GeoTIFF first.")
            return
        self.invalidate_calculation()
        self.dimensions.setText("Calculating final dimensions…")
        self.set_busy(True)
        self.log.clear()
        self.worker = Worker(Settings(**values), preview=True)
        self.worker.message.connect(self.log.appendPlainText)
        self.worker.calculated.connect(lambda result: self.show_dimensions(result, values))
        self.worker.failure.connect(self.calculation_failed)
        self.worker.finished.connect(self.finished)
        self.worker.start()

    def calculation_failed(self, error):
        self.dimensions.setText("Calculation failed — check the inputs and try again.")
        self.log.appendPlainText("Calculation failed: " + error)

    def show_dimensions(self, result, values):
        self.prepared, self.prepared_settings = result, values
        self.prepared_files = self.file_signature(values)
        m = result[1]
        self.dimensions.setText(
            f"Footprint (X × Y): {m['width']:.2f} × {m['length']:.2f} mm\n"
            f"Total height (Z, including base): {m['height']:.2f} mm\n"
            f"Base: {m['base']:.2f} mm + above-base terrain/features: {m['above_base']:.2f} mm\n"
            f"Horizontal scale: 1:{m['scale']:,.2f}  |  Vertical scale: 1:{m['vertical_scale']:,.2f}"
        )
        if 'grid_cols' in m:
            self.dimensions.setText(self.dimensions.text()+
                f"\nGrid: {m['grid_cols']} columns × {m['grid_rows']} rows; up to {m['grid_cols']*m['grid_rows']} tiles"
                f"\nEach cell: {m['tile_width']:.2f} × {m['tile_depth']:.2f} mm; empty AOI cells skipped")
        if 'contour_levels' in m:
            status = f"{m['contour_levels']} sampled elevation levels; assign filament to the Contours part" if m['contour_part'] else 'no visible contour inlays in the final model'
            if m.get('contour_operation')=='Cut only':
                status=f"{m['contour_levels']} sampled elevation levels; cut-only grooves (no colored part)"
            self.dimensions.setText(self.dimensions.text()+"\nContours: "+status)
        if m.get('major_levels'):
            self.dimensions.setText(self.dimensions.text()+f" | Major: {m['major_levels']}")
        if 'repair' in m:
            self.dimensions.setText(self.dimensions.text()+f"\nNoData: {m['repair']['repaired_cells']} sampled cells repaired; source unchanged.")
            self.repair_map_button.setEnabled(True)
        if 'assembly' in m:
            a=m['assembly']
            self.dimensions.setText(self.dimensions.text()+f"\nAssembly: {len(a['connectors'])} keys; {sum(v['status']=='placed' for v in a['labels'])} underside IDs; {a['skipped_connectors']} key positions skipped.")

    def file_signature(self, values):
        signature = []
        paths=[values[key] for key in ("dem", "gpkg", "aoi_gpkg")]+[v['gpkg'] for v in values.get('feature_layers',[])]
        for path in paths:
            if path:
                for name in (path, path+".aux.xml", path+"-wal"):
                    try:
                        stat = Path(name).stat()
                        signature.append((name, stat.st_size, stat.st_mtime_ns))
                    except OSError:
                        signature.append((name, None, None))
        return signature

    def run_export(self):
        values = self.values()
        if not values["dem"] or not values["output"]:
            QMessageBox.warning(self, "Missing files", "Choose an elevation GeoTIFF and output path.")
            return
        if values['export_mode'] == 'Print-bed grid':
            output = Path(values['output'])
            tile_folder = output.with_name(output.stem+'_tiles')
            if tile_folder.exists():
                QMessageBox.warning(self, "Tile folder exists", f"Choose a new output name. Existing tiles will be kept:\n{tile_folder}")
                return
        elif Path(values["output"]).exists() and QMessageBox.question(self, "Replace output?", "Replace the existing 3MF file?") != QMessageBox.StandardButton.Yes:
            return
        if self.prepared is not None and self.file_signature(values) != self.prepared_files:
            self.invalidate_calculation()
        self.set_busy(True)
        self.log.clear()
        self.worker = Worker(Settings(**values), prepared=self.prepared)
        self.worker.message.connect(self.log.appendPlainText)
        self.worker.success.connect(lambda result: self.log.appendPlainText(result + "\nImport each 3MF as one assembly and assign filament to each named part in your slicer."))
        self.worker.failure.connect(lambda error: self.log.appendPlainText("Export failed: " + error))
        self.worker.finished.connect(self.finished)
        self.worker.start()

    def finished(self):
        self.set_busy(False)

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "Export in progress", "Wait for the export to finish before closing.")
            event.ignore()
        else:
            event.accept()


def main():
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("TerrainForge.Desktop")
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(str(ICON_PATH)))
    app.setStyle("Fusion")
    window = Window()
    window.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
