from app.models import FileCategory
from app.naming import (
    classify_filename,
    extract_folder_name,
    parse_unity_and_pkg_version,
    parse_zip_version,
    pick_latest_unity_file,
)


def test_classify_filename():
    assert classify_filename("POLYGON_Military_ICON.png") == FileCategory.ICON
    assert classify_filename("POLYGON_Gang_Warfare_ICON.jpg") == FileCategory.ICON
    assert classify_filename("POLYGON_FrogShrine_Icon.png") == FileCategory.ICON
    assert classify_filename("POLYGON_Military_Unity_2022_3_v1_10_4.unitypackage") == FileCategory.UNITY
    assert classify_filename("POLYGON_Military_SourceFiles_v5.zip") == FileCategory.SOURCE_ZIP
    assert classify_filename("POLYGON_BattleRoyale_Source_Files_v4.zip") == FileCategory.SOURCE_ZIP
    assert classify_filename("INTERFACE_Apocalypse_HUD_Source_Sprites_v3.zip") == FileCategory.SOURCE_ZIP
    assert classify_filename("POLYGON_Military_Unreal_5_3_v1_10_4.zip") == FileCategory.IGNORED
    assert classify_filename("POLYGON_Military_UE5.zip") == FileCategory.IGNORED


def test_extract_folder_name():
    assert extract_folder_name("POLYGON_Military_ICON.png") == "POLYGON_Military"
    assert extract_folder_name("POLYGON_Military_Warehouse_Map_ICON.png") == "POLYGON_Military_Warehouse_Map"
    assert extract_folder_name("POLYGON_FrogShrine_Icon.png") == "POLYGON_FrogShrine"
    assert extract_folder_name("POLYGON_Gang_Warfare_ICON.jpg") == "POLYGON_Gang_Warfare"
    assert extract_folder_name("POLYGON_Military_Unity_2022_3_v1_10_4.unitypackage") == "POLYGON_Military"
    assert extract_folder_name("POLYGON_Holidays_2025_Unity_2022_3_v1_0_1_URP.unitypackage") == "POLYGON_Holidays_2025"
    assert extract_folder_name("POLYGON_Halloween_Masks_2022_3_v1_1_0.unitypackage") == "POLYGON_Halloween_Masks"
    assert extract_folder_name("SIMPLE_Cars_Unity_Project_2021_3_v1_4_0.unitypackage") == "SIMPLE_Cars"
    assert extract_folder_name("POLYGON_Dungeon_Pack_SourceFiles_v3.zip") == "POLYGON_Dungeon"
    assert extract_folder_name("POLYGON_WaterGuns_Unity_SourceFiles_v2.zip") == "POLYGON_WaterGuns"
    assert extract_folder_name("INTERFACE_Apocalypse_HUD_Source_Sprites_v3.zip") == "INTERFACE_Apocalypse_HUD"


def test_version_parsing_and_latest_selection():
    v_old = parse_unity_and_pkg_version("POLYGON_Military_Unity_2021_3_v1_9_0.unitypackage")
    v_mid = parse_unity_and_pkg_version("POLYGON_Military_Unity_2022_3_v1_9_0.unitypackage")
    v_new = parse_unity_and_pkg_version("POLYGON_Military_Unity_2022_3_v1_10_4.unitypackage")
    assert v_old < v_mid < v_new

    v_dot = parse_unity_and_pkg_version("SIMPLE_People_2_Unity_2020_3_v1.05.unitypackage")
    assert v_dot == ((2020, 3), (1, 5))

    candidates = [
        "POLYGON_Military_Unity_2021_3_v1_10_4.unitypackage",
        "POLYGON_Military_Unity_2022_3_v1_9_0.unitypackage",
        "POLYGON_Military_Unity_2022_3_v1_10_4.unitypackage",
    ]
    assert pick_latest_unity_file(candidates) == "POLYGON_Military_Unity_2022_3_v1_10_4.unitypackage"

    assert parse_zip_version("POLYGON_Military_SourceFiles_v5.zip") > parse_zip_version("POLYGON_Military_SourceFiles_v4.zip")
    assert parse_zip_version("SIMPLE_FX_SourceFiles.zip") == (0,)
