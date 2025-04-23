import os
import platform
import re
from typing import List, Dict
from fastmcp import FastMCP
import docker
import io
import tarfile
import uuid

DOCKER_IMAGE   = "rust:latest"

# TODO: Generalize this into a better workspace system
WORKSPACE_ROOT = os.path.normpath("/tmp/vibebolt/workspace")
ARTIFACT_ROOT = os.path.normpath("/tmp/vibebolt/artifacts")
# Ensure the workspace and artifact directories exist
os.makedirs(WORKSPACE_ROOT, exist_ok=True)
os.makedirs(ARTIFACT_ROOT, exist_ok=True)

mcp = FastMCP("Vibebolt Server")
docker_client = docker.from_env()

@mcp.resource("file://{path}")
def file_read(path: str) -> str:
    """
    Read a file from the workspace.
    Args:
        path (str): Relative path to the file to read, relative to the workspace root.
    """
    if os.path.isabs(path):
        raise ValueError("`path` must be relative")
    # Prevent escaping the workspace:
    full = os.path.normpath(os.path.join(WORKSPACE_ROOT, path))
    if not full.startswith(WORKSPACE_ROOT):
        raise ValueError("Path outside workspace")
    with open(full, "r") as f:
        return f.read()
    
@mcp.tool()
def file_write(path: str, content: str) -> bool:
    """
    Write content to a file in the workspace. If the file exists, it will be overwritten.
    Args:
        path (str): Relative path to the file to write, relative to the workspace root.
        content (str): The content to write to the file.
    """
    if os.path.isabs(path):
        raise ValueError("`path` must be relative")
    full = os.path.normpath(os.path.join(WORKSPACE_ROOT, path))
    if not full.startswith(WORKSPACE_ROOT):
        raise ValueError("Path outside workspace")
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(content)
    return True

@mcp.tool()
def file_delete(path: str) -> bool:
    """
    Delete a file in the workspace.
    Args:
        path (str): Relative path to the file to delete, relative to the workspace root.
    """
    if os.path.isabs(path):
        raise ValueError("`path` must be relative")
    full = os.path.normpath(os.path.join(WORKSPACE_ROOT, path))
    if not full.startswith(WORKSPACE_ROOT):
        raise ValueError("Path outside workspace")
    os.remove(full)
    return True

@mcp.tool()
def reset_workspace():
    """
    Cleans the workspace by removing all files and directories.
    """
    for root, dirs, files in os.walk(WORKSPACE_ROOT, topdown=False):
        for name in files:
            os.remove(os.path.join(root, name))
        for name in dirs:
            os.rmdir(os.path.join(root, name))
    return True

@mcp.tool()
def file_list(dir_path: str) -> List[str]:
    "List all files in workspace"
    full = os.path.normpath(os.path.join(WORKSPACE_ROOT, dir_path))
    if not full.startswith(WORKSPACE_ROOT):
        raise ValueError("Path outside workspace")
    return os.listdir(full)


def clear_artifact_cache():
    """
    Cleans the artifact cache by removing all files and directories.
    """
    for root, dirs, files in os.walk(ARTIFACT_ROOT, topdown=False):
        for name in files:
            os.remove(os.path.join(root, name))
        for name in dirs:
            os.rmdir(os.path.join(root, name))

def fix_docker_volume_path(path_str):
    """
    Convert a host path to Docker-compatible volume mount format,
    especially important for Windows compatibility.
    """
    # Normalize path first
    path_str = os.path.normpath(path_str)
    
    if platform.system() == "Windows":
        # Handle Windows drive letters for Docker (C:\ -> /c/)
        if re.match(r'^[A-Za-z]:\\', path_str):
            drive_letter = path_str[0].lower()
            path_without_drive = path_str[2:].replace('\\', '/')
            return f"/{drive_letter}{path_without_drive}"
        else:
            # Handle other Windows paths - replace backslashes
            return path_str.replace('\\', '/')
    
    # Non-Windows paths are returned as-is
    return path_str

class DockerVolume:
    """
    Context manager which creates and destroys a docker volume.
    """
    def __init__(self, delete_on_exit=True, volume_name=None):
        self.volume_name = volume_name
        self.delete_on_exit = delete_on_exit

    def __enter__(self):
        # Create the volume mount
        if self.volume_name is None:
            self.session_id = str(uuid.uuid4())
            self.volume_name = f"vibebolt_volume_{self.session_id}"
            self.volume = docker_client.volumes.create(self.volume_name)
        return self.volume_name, self.session_id

    def __exit__(self, exc_type, exc_value, traceback):
        # Clean up the volume mount
        if self.delete_on_exit:
            docker_client.volumes.get(self.volume_name).remove(force=True)

# Yes, this lets AI execute arbitrary code. But we trust them, right?
@mcp.tool()
def build_and_run_code(entry, opt_level="0", compile_args=[], run_args=[], input=None, iterations=100, profile=True, delete_volumes_on_exit=True, delete_containers_on_exit=True) -> Dict:
    """
    Compiles the contents of the current workspace inside a docker container using `rustc`, and then
    runs the compiled binary with the provided arguments and input. Returns the logs, exit code, and metrics for the build and run.

    Args:
        entry (str): The path to the Rust source file to compile, relative to the workspace root.
        opt_level (str): The optimization level to use for the Rust compiler. Possible levels are 0-3, s, or z (default: "0")
        compile_args (List[str]): Arguments to pass to the Rust compiler.
        run_args (List[str]): Arguments to pass to the compiled binary.
        input (str): Input to pass to the compiled binary.
        iterations (int): Number of iterations to run the binary for. (Unimplemented)
        profile (bool): Whether to profile the run. (Unimplemented)
        delete_volumes_on_exit (bool): Whether to delete the volumes after use.
        delete_containers_on_exit (bool): Whether to delete the containers after use.
    """
    # Resolve path in workspace
    if os.path.isabs(entry):
        raise ValueError("`entry` must be relative")
    src = os.path.normpath(os.path.join(WORKSPACE_ROOT, entry))
    if not src.startswith(WORKSPACE_ROOT):
        raise ValueError("Entry path outside workspace")
    # Pre-run clear artifact cache
    clear_artifact_cache()

    with DockerVolume(delete_on_exit=delete_volumes_on_exit) as (volume_name, session_id):
        # Transfer over files to the docker volume and extract 
        # TODO: Reorganize workspace and artifact so it's less of a mess
        volume = docker_client.volumes.get(volume_name)
        placer_container = None
        try:
            placer_container = docker_client.containers.run(
                DOCKER_IMAGE,
                name=f"placer_container_{session_id}",
                command="sleep 3600",
                working_dir="/workspace",
                volumes={
                    volume_name: {
                        "bind": "/workspace",
                        "mode": "rw"
                    }
                },
                network_disabled=True,
                detach=True,
            )

            workspace_buf = io.BytesIO()
            with tarfile.open(fileobj=workspace_buf, mode="w") as tar:
                tar.add(WORKSPACE_ROOT, arcname=".")
                workspace_buf.seek(0)
                placer_container.put_archive("/workspace", workspace_buf)

            placer_container.exec_run("mkdir -p /workspace/artifacts")
            if input:
                # Write input to the artifacts directory
                with open(os.path.join(ARTIFACT_ROOT, "input.txt"), "w") as f:
                    f.write(input)
                # Copy input file to the docker volume
                input_buf = io.BytesIO()
                with tarfile.open(fileobj=input_buf, mode="w") as tar:
                    tar.add(os.path.join(ARTIFACT_ROOT, "input.txt"), arcname="input.txt")        
                    input_buf.seek(0)
                    placer_container.put_archive("/workspace/artifacts", input_buf)
        finally:
            # Ensure the container is removed after use
            if placer_container:
                placer_container.remove(force=True)
        
        # Containerize build
        # Prepare build command
        binary_artifact = "/workspace/artifacts/a.out"
        build_cmd = ["rustc", entry, "-o", binary_artifact] + ["-C", f"opt-level={opt_level}"] + compile_args

        build_container = None
        try:
            build_container = docker_client.containers.run(
                DOCKER_IMAGE,
                name=f"build_container_{session_id}",
                command=build_cmd,
                volumes={
                    volume_name: {
                        "bind": "/workspace",
                        "mode": "rw"
                    },
                },
                working_dir="/workspace",
                network_disabled=True,
                detach=True,
            )
            build_code = build_container.wait()["StatusCode"]
            build_logs = build_container.logs(stdout=True, stderr=True).decode()
        finally:
            # Ensure the container is removed after use
            if build_container:
                build_container.remove(force=True)
        results = {
                "build_success": build_code == 0,
                "build_logs": build_logs,
                "build_code": build_code
        }
        if not results["build_success"]:
            # Build failed, return logs
            return results

        # Post run clear artifact cache
        # Fix for input redirection
        if input:
            # Use shell to handle input redirection
            run_cmd = ["sh", "-c", f"/workspace/artifacts/a.out {' '.join(run_args)} < /workspace/artifacts/input.txt"]
        else:
            run_cmd = ["/workspace/artifacts/a.out"] + run_args

        # Containerize run
        run_container = None
        try:
            run_container = docker_client.containers.run(
                DOCKER_IMAGE,
                name=f"run_container_{session_id}",
                command=run_cmd,
                volumes={
                    volume_name: {
                        "bind": "/workspace",
                        "mode": "rw"
                    }
                },
                working_dir="/workspace",
                network_disabled=True,
                detach=True,
            )

            run_code = run_container.wait()["StatusCode"]
            run_logs = run_container.logs(stdout=True, stderr=True).decode()
            results["run_logs"] = run_logs
            results["run_code"] = run_code
        finally:
            # Ensure the container is removed after use
            if run_container:
                run_container.remove(force=True)

        return results
