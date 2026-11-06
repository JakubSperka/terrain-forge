import os
import gc
import pytest
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase, QFont
from PySide6.QtCore import QSettings, QCoreApplication, QEvent
from PySide6.QtWidgets import QFileDialog, QMessageBox
from terrain_forge.desktop import Window

APP = QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def cleanup_windows():
    yield
    for widget in APP.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    APP.processEvents()
    gc.collect()


def test_desktop_controls(tmp_path):
    app = APP
    font_id = QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
    if font_id >= 0:
        app.setFont(QFont(QFontDatabase.applicationFontFamilies(font_id)[0], 10))
    window = Window()
    window.show()
    app.processEvents()
    assert window.fields["mode"].count() == 4
    assert window.fields["size_mode"].count() == 3
    assert window.export.isEnabled()
    assert not window.windowIcon().isNull()
    assert not window.fields['bed_width'].isEnabled()
    assert window.grab().save(str(tmp_path / "desktop.png"))
    window.close()


def test_settings_roundtrip_and_directory(tmp_path, monkeypatch):
    app = APP
    preferences = QSettings(str(tmp_path/'preferences.ini'), QSettings.Format.IniFormat)
    window = Window(preferences)
    path = tmp_path/'terrain-settings.json'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a: (str(path), ''))
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a: (str(path), ''))
    window.fields['dem'].setText(str(tmp_path/'terrain.tif'))
    window.fields['size_mode'].setCurrentText('Scale denominator')
    window.fields['size'].setValue(25000)
    window.fields['exaggeration'].setValue(2.5)
    window.fields['baseline_mode'].setCurrentText('Custom elevation')
    window.fields['baseline_elevation'].setValue(100)
    window.fields['export_mode'].setCurrentText('Print-bed grid')
    window.fields['bed_width'].setValue(256)
    window.fields['bed_margin'].setValue(0)
    window.fields['contours'].setCurrentText('Enabled')
    window.fields['contour_operation'].setCurrentText('Recessed channel')
    window.fields['contour_thickness'].setValue(0.6)
    window.fields['contour_interval'].setValue(25)
    window.fields['major_contours'].setCurrentText('Enabled')
    window.fields['major_every'].setValue(4)
    window.fields['tile_connectors'].setCurrentText('Underside keys')
    window.fields['tile_labels'].setCurrentText('Embossed badge')
    window.fields['nodata_repair'].setCurrentText('Small enclosed holes')
    expected = window.values()
    window.save_settings()
    window.fields['size'].setValue(180)
    window.fields['exaggeration'].setValue(1)
    window.load_settings()
    assert window.values() == expected
    assert window.fields['bed_width'].isEnabled()
    assert window.fields['contour_interval'].isEnabled()
    assert window.fields['baseline_elevation'].isEnabled()
    window.close()
    reopened = Window(QSettings(str(tmp_path/'preferences.ini'), QSettings.Format.IniFormat))
    assert reopened.dialog_start() == str(tmp_path)
    reopened.close()


def test_invalid_settings_do_not_change_form(tmp_path, monkeypatch):
    app = APP
    window = Window(QSettings(str(tmp_path/'preferences.ini'), QSettings.Format.IniFormat))
    path = tmp_path/'bad.json'
    path.write_text('{"version":1,"settings":{"size":-5,"base":100}}')
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a: (str(path), ''))
    errors = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a: errors.append(a[2]))
    before = window.values()
    window.load_settings()
    assert window.values() == before
    assert errors
    window.close()


def test_dimensions_become_stale_on_model_edits(tmp_path):
    app = APP
    window = Window(QSettings(str(tmp_path/'preferences.ini'), QSettings.Format.IniFormat))
    result = ([], dict(width=180,length=90,height=25,base=3,above_base=22,scale=25000,vertical_scale=12500))
    window.show_dimensions(result, window.values())
    assert '25.00 mm' in window.dimensions.text()
    window.fields['output'].setText(str(tmp_path/'new.3mf'))
    assert window.prepared is result
    window.fields['base'].setValue(5)
    assert window.prepared is None
    assert 'not current' in window.dimensions.text()
    window.close()


def test_feature_list_edit_reorder_save_and_remove(tmp_path,monkeypatch):
    import geopandas as gpd
    from shapely.geometry import LineString
    gpkg=tmp_path/'features.gpkg'
    for name in ('river','roads'):
        gpd.GeoDataFrame(geometry=[LineString([(0,0),(10,10)])],crs='EPSG:32633').to_file(gpkg,layer=name,driver='GPKG')
    window=Window(QSettings(str(tmp_path/'preferences.ini'),QSettings.Format.IniFormat))
    window.fields['gpkg'].setText(str(gpkg))
    window.layers()
    window.fields['layer'].setCurrentText('river')
    window.add_feature()
    window.add_feature()  # Copy the settings, then change the new entry.
    window.fields['layer'].setCurrentText('roads')
    window.fields['mode'].setCurrentText('Raised feature')
    window.fields['depth'].setValue(2)
    assert window.feature_layers[0]['layer']=='river'
    assert window.feature_layers[1]['layer']=='roads'
    assert window.feature_layers[1]['depth']==2
    window.move_feature(-1)
    assert window.feature_layers[0]['layer']=='roads'
    expected=window.values()
    path=tmp_path/'multi.json'
    monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *a:(str(path),''))
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *a:(str(path),''))
    window.save_settings()
    window.remove_feature()
    window.load_settings()
    assert window.values()==expected
    window.feature_table.selectRow(0)
    window.remove_feature()
    window.feature_table.selectRow(0)
    window.remove_feature()
    assert not window.feature_layers
    assert window.fields['gpkg'].text()==''
    window.close()
