import os
import platform
import re
from typing import List, Dict
import sys
from fastmcp import FastMCP
import docker

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

# Yes, this lets AI execute arbitrary code. But we trust them, right?
@mcp.tool()
def build_and_run_code(entry, opt_level="O0", args=[], input=None, iterations=100, profile=True) -> Dict:
    """
    Compiles the contents of the current workspace inside a docker container using `rustc`, and then
    runs the compiled binary with the provided arguments and input. Returns the logs, exit code, and metrics for the build and run.

    Args:
        entry (str): The path to the Rust source file to compile, relative to the workspace root.
        opt_level (str): The optimization level to use for the Rust compiler. (default: "O0")
        args (List[str]): Arguments to pass to the compiled binary.
        input (str): Input to pass to the compiled binary.
        iterations (int): Number of iterations to run the binary for. (Unimplemented)
        profile (bool): Whether to profile the run. (Unimplemented)
    """
    # Resolve path in workspace
    if os.path.isabs(entry):
        raise ValueError("`entry` must be relative")
    src = os.path.normpath(os.path.join(WORKSPACE_ROOT, entry))
    if not src.startswith(WORKSPACE_ROOT):
        raise ValueError("Entry path outside workspace")
    # Pre-run clear artifact cache
    clear_artifact_cache()

    # Prepare build command
    binary_artifact = "/artifacts/bin/a.out"
    build_cmd = ["rustc", entry, "-o", binary_artifact] + ["-C", f"opt-level={opt_level}"]

    docker_workspace = fix_docker_volume_path(WORKSPACE_ROOT)
    docker_artifacts = fix_docker_volume_path(ARTIFACT_ROOT)
    # Containerize build
    build_container = docker_client.containers.run(
        DOCKER_IMAGE,
        command=build_cmd,
        volumes={
            docker_workspace: {
                "bind": "/workspace",
                "mode": "ro"
            },
            docker_artifacts: {
                "bind": "/artifacts",
                "mode": "rw"
            }
        },
        working_dir="/workspace",
        network_disabled=True,
    )

    build_logs = build_container.logs(stdout=True, stderr=True).decode()
    build_code = build_container.wait()["StatusCode"]
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
        # Create an input file instead of using shell redirection
        input_file = os.path.join(ARTIFACT_ROOT, "input.txt")
        with open(input_file, "w") as f:
            f.write(input)
        
        # Use shell to handle input redirection
        run_cmd = ["sh", "-c", f"/artifacts/bin/a.out {' '.join(args)} < /artifacts/input.txt"]
    else:
        run_cmd = ["/artifacts/bin/a.out"] + args

    run_container = docker_client.containers.run(
        DOCKER_IMAGE,
        command=[" ".join(run_cmd)],
        volumes={
            docker_artifacts: {
                "bind": "/artifacts",
                "mode": "rw"
            }
        },
        working_dir="/artifacts",
        network_disabled=True,
    )

    run_logs = run_container.logs(stdout=True, stderr=True).decode()
    run_code = run_container.wait()["StatusCode"]
    results["run_logs"] = run_logs
    results["run_code"] = run_code

    return results
