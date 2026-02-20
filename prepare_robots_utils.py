
# Longer, detailed macro description

__Name__ = ''
__Comment__ = ''
__Author__ = ''
__Version__ = ''
__Date__ = 'YYYY-MM-DD'
__License__ = 'LGPL-2.0-or-later'
__Web__ = 'http://forum.freecadweb.org/viewtopic.php?f=?&t=????'
__Wiki__ = 'http://www.freecadweb.org/wiki/Macro_Title_Of_macro'
__Icon__ = ''
__Help__ = ''
__Status__ = ''
__Requires__ = 'FreeCAD ?.??'
__Communication__ = 'https://github.com/FreeCAD/FreeCAD-macros/issues/'
__Files__ = ''

import csv
import gzip
import re
import shutil
import zipfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

import FreeCAD as app
import FreeCADGui as gui
import numpy as np
import requests
from fpo import Preference
from FreeCAD import Matrix, Placement, Rotation, Vector
from freecad import module_io
from numpy.typing import ArrayLike
from PySide import QtCore, QtWidgets  # FreeCAD's PySide!

# Typing hints.
HMatrix = ArrayLike  # A 4x4 homogeneous matrix.


@dataclass
class DHFrame:
    """
    A Denavit-Hartenberg frame.

    Only revolute joints are supported.
    """
    # Translation along the original z axis, in millimeters.
    d: float

    # Rotation about the original z axis, in degrees.
    # The actuation of the joint will be added to this parameter.
    theta: float

    # Translation along the new x axis (sometimes called `a`), in millimeters.
    r: float

    # Rotation about the new x axis, in degrees.
    alpha: float

    # Mechanical joint parameters, in ° and °/s.
    min_angle: float
    max_angle: float
    velocity: float


@dataclass
class Robot:
    """A robot defined by Denavit-Hartenberg parameters."""
    frames: list[DHFrame]


@dataclass
class ImportOptions:
    """User options to import robot models."""
    format: str = "STEP"
    step_merge_compounds: bool | None = True


X_AXIS = Vector(1, 0, 0)
Y_AXIS = Vector(0, 1, 0)
Z_AXIS = Vector(0, 0, 1)


def download_original(
        url: str,
        member_paths: list[str],
        dest_dir_path: Path,
        format: str = "zip",
) -> list[Path] | None:
    """Download and extract the original robot model files.

    Args:
        url: URL to download the archive from.
        member_paths: List of relative file paths inside the archive to extract.
        dest_dir_path: Directory to store the downloaded and extracted files.
        format: Archive format (defaults to "zip").

    Returns:
        The path to the extracted files.
    """
    local_filename = dest_dir_path / f"downloaded_file.{format}"
    if not download_file(url, local_filename):
        app.Console.PrintError(f'Cannot download file from URL: "{url}"\n')
        return

    # Extract the files.
    dest_paths: list[Path] = []
    for member_path in member_paths:
        dest_path = dest_dir_path / member_path
        try:
            extract_single_file(local_filename, member_path, dest_path)
        except ValueError as e:
            app.Console.PrintError(f"Extraction failed: {e}\n")
            return None
        dest_paths.append(dest_path)
    return dest_paths


def user_download_original(
        url: str,
        filename: str,
        member_paths: list[str],
        dest_dir_path: Path,
        format: str = "zip",
) -> list[Path] | None:
    """
    Request the user to download and extract the original robot model files.

    Args:
        url: URL to download the archive from.
        filename: Expected filename for the downloaded file.
        member_paths: List of relative file paths inside the archive to extract.
        dest_dir_path: Directory to store the downloaded and extracted files.
        format: Archive format (defaults to "zip").
    """
    archive_path = request_download(url, filename)
    if not archive_path:
        app.Console.PrintError("Cancelled on user request\n")
        return

    # Extract the files.
    dest_paths: list[Path] = []
    for member_path in member_paths:
        dest_path = dest_dir_path / member_path
        try:
            extract_single_file(archive_path, member_path, dest_path)
        except ValueError as e:
            app.Console.PrintError(f"Extraction failed: {e}\n")
            return None
        dest_paths.append(dest_path)
    return dest_paths


def request_download(url: str, filename: str) -> Path | None:
    """
    Invite the user to download a file from a URL.

    Args:
        url: URL to download the file from.
        filename: Suggested filename for the downloaded file.
    """
    url_diag = QtWidgets.QMessageBox()
    url_diag.setIcon(QtWidgets.QMessageBox.Information)
    url_diag.setWindowTitle("Download required file")
    url_diag.setTextFormat(QtCore.Qt.RichText)
    url_diag.setText(f'The file "{filename}" must be downloaded (credentials may be required).\n\n'
                     f'Please download it from:\n<a href="{url}">{url}</a>')
    url_diag.setStandardButtons(QtWidgets.QMessageBox.Ok | QtWidgets.QMessageBox.Cancel)
    ret = url_diag.exec_()
    if ret != QtWidgets.QMessageBox.Ok:
        app.Console.PrintMessage("Operation cancelled by user.\n")
        return None

    app_gui = gui.getMainWindow()
    extension_suggestion = Path(filename).suffix
    file_path_dialog = QtWidgets.QFileDialog(
        app_gui,
        f'Select the location of the saved file "{filename}"',
        "",
        f"*{extension_suggestion}",
    )

    if file_path_dialog.exec_() != QtWidgets.QFileDialog.Accepted:
        return None

    return Path(file_path_dialog.selectedFiles()[0])


def extract_single_file_zip(
        archive_path: Path,
        member_path: str | Path,
        dest_path: Path,
) -> None:
    """Extract a single file from a ZIP archive.

    Args:
        archive_path: Path to the ZIP archive.
        member_path: Path of the file inside the archive to extract.
        dest_path: Destination file.
    """
    member_path = str(member_path)
    if not zipfile.is_zipfile(archive_path):
        raise ValueError(f'"{archive_path}" is not a valid ZIP file.')
    with zipfile.ZipFile(archive_path, "r") as zip_f:
        if member_path not in zip_f.namelist():
            raise ValueError(f'"{member_path}" not found in "{archive_path}".')
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        zip_f.extract(member_path, dest_path.parent)
        extracted_file = dest_path.parent / member_path
        extracted_file.rename(dest_path)


def extract_single_file(
        archive_path: Path,
        member_path: str | Path,
        dest_path: Path,
        ) -> None:
    """Extract a single file from an archive (ZIP or TAR).

    Args:
        archive_path: Path to the ZIP archive.
        member_path: Path of the file inside the archive to extract.
        dest_path: Destination file.
    """
    with suppress(ValueError):
        extract_single_file_zip(archive_path, member_path, dest_path)
        return
    raise ValueError(f'No known method to extract "{member_path}" from "{archive_path}".')


def download_file(url: str, local_path: Path) -> bool:
    """Download a file from a URL to a local path."""
    try:
        with requests.get(url, stream=True) as r:
            r.raise_for_status()
            with open(local_path, 'wb') as f:
                shutil.copyfileobj(r.raw, f)
        app.Console.PrintMessage(f"Downloaded file to: {local_path}\n")
        return True
    except Exception as e:
        app.Console.PrintError(f"Download failed: {e}\n")
        return False


def import_original(
        doc: app.Document,
        paths: Path | list[Path],
        import_options: ImportOptions,
) -> None:
    """
    Import the original robot model files into the document.

    Args:
        doc: FreeCAD document to import the model into.
        paths: List of file paths to import.
        import_options: User options for importing.
    """
    if not isinstance(paths, list):
        paths = [paths]

    old_import_options = save_import_options()
    write_import_options(import_options)

    for path in paths:
        app.Console.PrintMessage(f'Opening {import_options.format.upper()} file: "{path}"\n')
        module_io.OpenInsertObject("Import", str(path), "insert", doc.Name)
    doc.recompute()
    write_import_options(old_import_options)


def save_import_options() -> ImportOptions:
    """
    Save the user import preferences into an ImportOptions dataclass.

    This only reads preferences; it does not modify them.
    A default is set by the function if the user parameter is not set but there is not
    way to get the information whether this parameter was set by the user or not.
    """
    import_options = ImportOptions()
    # Implementation note: `()` calls the getter of the preference.
    import_options.step_merge_compounds = Preference(
            group="Preferences/Mod/Import/hSTEP",
            name="ReadShapeCompoundMode",
            default=True,
    )()
    return import_options


def write_import_options(import_options: ImportOptions) -> None:
    # Implementation note: we must use `default` otherwise, the type will be `str`
    # and corresponds then to another parameter with the same group and name but with
    # different type.
    step_merge_compounds_pref = Preference(
            group="Preferences/Mod/Import/hSTEP",
            name="ReadShapeCompoundMode",
            default=import_options.step_merge_compounds,
    )
    step_merge_compounds_pref.write(import_options.step_merge_compounds)


def save_wrl(obj: app.DocumentObject, file_path: Path) -> None:
    """Save a FreeCAD object to a VRML file (clear text)."""
    # Even if the extension is `wrl`, FreeCAD exports in `wrz` format (compressed).
    # So first export to `wrz`, then uncompress to `wrl`.
    wrz_path = file_path.with_suffix('.wrz')
    app.Console.PrintMessage(f'Saving WRZ file to: "{file_path}"\n')
    gui.export([obj], str(wrz_path))

    # Uncmpress WRZ (gzipped) to WRL (clear text).
    with gzip.open(wrz_path, "r") as f_in:
        with file_path.open("wb") as f_out:
            shutil.copyfileobj(f_in, f_out)


def save_obj_as_wrl(
        doc: app.Document,
        label: str,
        placement: Placement,
        directory: Path,
        ) -> Path:
    """Export a FreeCAD object with the given label to a WRL file."""
    objs = doc.getObjectsByLabel(label)
    if not objs:
        raise ValueError(f'Object with label "{label}" not found in document.')
    if len(objs) > 1:
        # Prefer to raise an error than exporting an unexpected object.
        # By default, labels are unique in FreeCAD but the user may have
        # changed the configuration to allow duplicate labels.
        raise ValueError(f'Multiple objects with label "{label}" found in document.')
    obj = objs[0]
    obj.Placement = placement
    obj.Document.recompute()
    base_wrl = directory / f"{obj.Name}.wrl"
    save_wrl(obj, base_wrl)
    return base_wrl


def translation(placement: Placement) -> Placement:
    """Return a Placement with only the translation of the given Placement."""
    return Placement(placement.Base, Rotation())


def rotation(placement: Placement) -> Placement:
    """Return a Placement with only the rotation of the given Placement."""
    return Placement(Vector(0.0, 0.0, 0.0), placement.Rotation)


def save_files(files: list[Path], dest_dir: Path) -> None:
    """Open a dialog to save files to a user-selected directory."""
    app_gui = gui.getMainWindow()
    dest_dir_str = QtWidgets.QFileDialog.getExistingDirectory(
        app_gui,
        "Select directory to save files",
        str(dest_dir),
        QtWidgets.QFileDialog.ShowDirsOnly | QtWidgets.QFileDialog.DontResolveSymlinks,
    )
    if not dest_dir_str:
        app.Console.PrintMessage("Operation cancelled by user.\n")
        return
    dest_dir_path = Path(dest_dir_str)
    for file_path in files:
        dest_path = dest_dir_path / file_path.name
        shutil.copy(file_path, dest_path)
        app.Console.PrintMessage(f'Saved file to: "{dest_path}"\n')


def include_vrml_file(
    file_path: Path,
    axis: int,
    joint_transform: Placement,
    indent: str = "",
) -> (str, str):
    """Return the VRML code to include a VRML file with joint transform.

    This assumes that the mesh in the VRML file is defined in the global frame when the
    joint position of all joint are zero.

    Args:
        file_path: Path to the VRML file to include.
        axis: Joint axis index (for naming axes in the whole VRML according to FreeCAD's
              requirements, 1-based).
        joint_transform: Placement representing the joint transform (not the
                         Denavit-Hartenberg frame) in the global frame.
                         The z-axis is the joint axis.
                         Only prismatic joints are supported.
        indent: optional indent to add at the beginning of each line (VRML is not
                sensitive to indent).

    Returns:
        A tuple of two strings: (before, after), where `before` is the VRML code
        to add where the joint "starts", and `after` is the VRML code to add after
        all child joint are added.
    """
    v = joint_transform.Base
    a = joint_transform.Rotation.Axis
    theta = joint_transform.Rotation.Angle  # radians (as of 2025).

    before = ""
    after = ""
    before += f"{indent}# {file_path.name}\n"
    before += f"{indent}Transform {{\n"  # Level 1.
    indent += "  "  # A copy.
    before += f"{indent}translation {v.x} {v.y} {v.z}\n"
    before += f"{indent}rotation {a.x} {a.y} {a.z} {theta}\n"
    before += f"{indent}children [\n"  # Level 2.
    indent += "  "
    before += f"{indent}DEF FREECAD_AXIS{axis} Transform {{\n"  # Level 3.
    indent += "  "
    before += f"{indent}rotation 0 0 1 0\n"
    indent += "  "
    before += f"{indent}children [\n"  # Level 4.
    indent += "  "
    before += f"{indent}Transform {{\n"  # Level 5.
    indent += "  "
    before += f"{indent}rotation {a.x} {a.y} {a.z} {-theta}\n"
    indent += "  "
    before += f"{indent}children [\n"  # Level 6.
    indent += "  "
    before += f"{indent}Transform {{\n"  # Level 7.
    indent += "  "
    before += f"{indent}translation {-v.x} {-v.y} {-v.z}\n"
    before += f"{indent}children [\n"  # Level 8.
    indent += "  "
    # Replace the `DEF o?+` definition generated by FreeCAD with
    # `DEF {file_path.stem}_o...` to avoid name clashes
    # (also change `USE o?+`).
    regex_def = re.compile(r"DEF\s+o([0-9]+)")
    regex_use = re.compile(r"USE\s+o([0-9]+)")
    with file_path.open("r") as f:
        for line in f:
            line = regex_def.sub(f"DEF {file_path.stem}_o\\1", line)
            line = regex_use.sub(f"USE {file_path.stem}_o\\1", line)
            before += indent + line + "\n"
    indent = indent[:-2]
    after += f"{indent}]\n"  # Level 8. Close children.
    indent = indent[:-2]
    after += f"{indent}}}\n"  # Level 7. Close Transform.
    indent = indent[:-2]
    after += f"{indent}]\n"  # Level 6. Close children.
    indent = indent[:-2]
    after += f"{indent}}}\n"  # Level 5. Close Transform.
    indent = indent[:-2]
    after += f"{indent}]\n"  # Level 4. Close children.
    indent = indent[:-2]
    after += f"{indent}}}\n"  # Level 3. Close Transform.
    indent = indent[:-2]
    after += f"{indent}]\n"  # Level 2.Close children.
    indent = indent[:-2]
    after += f"{indent}}}\n"  # Level 1. Close Transform.
    return before, after


def write_csv(
        file_path: Path,
        robot: Robot,
    ) -> None:
    """Write the robot's DH parameters to a CSV file.

    Args:
        robot: Robot defined by Denavit-Hartenberg parameters.
        file_path: Path to the output CSV file.
    """
    with file_path.open("w", newline="") as csvfile:
        csv_writer = csv.writer(csvfile)
        # Write the header.
        # The format is determined by FreeCAD's robot workbench.
        # Units: mm, deg, mm, deg, 1, deg, deg, deg/s.
        csv_writer.writerow(["a", "alpha", "d", "theta", "rotDir", "maxAngle", "minAngle", "AxisVelocity"])
        # Write each frame's parameters.
        for frame in robot.frames:
            csv_writer.writerow([
                f"{frame.r}",
                f"{frame.alpha}",
                f"{frame.d}",
                f"{frame.theta}",
                "1.0",
                f"{frame.max_angle}",
                f"{frame.min_angle}",
                f"{frame.velocity}",
            ])


def matrix_dh(d: float, theta: float, r: float, alpha: float) -> HMatrix:
    """
    Return a 4x4 homogeneous matrix for Denavit-Hartenberg parameters.

     Return
     ⎡ R | T ⎤
     ⎣ 0 | 1 ⎦
     or, in details,:
         ⎡ ct -st*ca  st*sa a*ct ⎤
         | st  ct*ca -ct*sa a*st |
         |  0     sa     ca    d |
         ⎣  0      0      0    1 ⎦

     Parameters
     ==========
     - d: Translation along the original z axis, in user units.
     - theta: Rotation about the original z axis, in radians.
              The actuation of the joint must be added to this parameter.
     - r: Translation along the new x axis (sometimes called `a`), in user units.
     - alpha: Rotation about the new x axis, in radians.

    """
    ct = np.cos(theta)
    st = np.sin(theta)
    ca = np.cos(alpha)
    sa = np.sin(alpha)
    return np.array([
        [ct, -st*ca,  st*sa, r*ct],
        [st,  ct*ca, -ct*sa, r*st],
        [ 0,     sa,     ca,    d],
        [ 0,      0,      0,    1],
    ])


def placement_dh_deg(d: float, theta_deg: float, r: float, alpha_deg: float) -> Placement:
    """Return a FreeCAD Placement for Denavit-Hartenberg parameters (degrees)."""
    theta_rad = np.radians(theta_deg)
    alpha_rad = np.radians(alpha_deg)
    dh_array = matrix_dh(d=d, theta=theta_rad, r=r, alpha=alpha_rad)
    return Placement(Matrix(*dh_array.flatten()))
