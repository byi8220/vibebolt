import os
import platform
import re
from typing import List, Dict
from mcp.server.fastmcp import FastMCP
import docker
import io
import tarfile
import uuid

DOCKER_IMAGE   = "rust:latest"

# TODO: Generalize this into a better workspace system
WORKSPACE_ROOT = os.path.normpath(os.path.join(os.path.expanduser("~"), "/tmp/vibebolt/workspace"))
ARTIFACT_ROOT = os.path.normpath(os.path.join(os.path.expanduser("~"), "/tmp/vibebolt/artifacts"))
# Ensure the workspace and artifact directories exist
os.makedirs(WORKSPACE_ROOT, exist_ok=True)
os.makedirs(ARTIFACT_ROOT, exist_ok=True)

mcp = FastMCP("Vibebolt Server")
try:
    docker_client = docker.from_env()
except docker.errors.DockerException as e:
    raise RuntimeError("Docker is not running or not accessible. Please ensure Docker is installed and running.") from e

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
def build_and_run_code(entry, opt_level="2", compile_args=[], run_args=[], input=None,
                       build_env_vars={}, run_env_vars={}, additional_compiler_outputs=[],
                       delete_volumes_on_exit=True, delete_containers_on_exit=True, only_get_asm=False) -> Dict:
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
            if placer_container and delete_containers_on_exit:
                placer_container.remove(force=True)
        
        # Containerize build
        # Prepare build command
        binary_artifact = "/workspace/artifacts/a.out"
        emission_types = []
        if type(compile_args) is str:
            compile_args = [compile_args]

        if any("--emit" in arg for arg in compile_args):
            raise ValueError("Do not manually specify the `--emit` compiler flag in compile_args. Specify `additional_compiler_outputs` instead, or use `get_asm`.")
        if any("opt-level" in arg for arg in compile_args):
            raise ValueError("Do not manually specify the `opt-level` compiler flag in compile_args. Specify `opt_level` instead.")

        modified_compile_args = compile_args.copy()
        if "llvm_ir" in additional_compiler_outputs:
            emission_types.append("llvm-ir")
        if "asm" in additional_compiler_outputs:
            emission_types.append("asm")
        if "mir" in additional_compiler_outputs:
            emission_types.append("mir")
        
        if emission_types:
            modified_compile_args.extend(["--emit", ",".join(emission_types)])
        build_cmd = ["rustc", entry, "-o", binary_artifact] + ["-C", f"opt-level={opt_level}"] + modified_compile_args

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
                environment=build_env_vars,
                working_dir="/workspace",
                network_disabled=True,
                detach=True,
            )
            build_code = build_container.wait()["StatusCode"]
            build_logs = build_container.logs(stdout=True, stderr=True).decode()
        finally:
            # Ensure the container is removed after use
            if build_container and delete_containers_on_exit:
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
        if not only_get_asm:
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
                    environment=run_env_vars,
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
                if run_container and delete_containers_on_exit:
                    run_container.remove(force=True)

        # Collect additional outputs if requested
        def extract_file_from_archive(archive_bits):
            """Extract the content of a file from a Docker archive."""
            file_data = io.BytesIO()
            for chunk in archive_bits:
                file_data.write(chunk)
            file_data.seek(0)
            
            with tarfile.open(fileobj=file_data) as tar:
                # The file will be inside a directory in the archive
                for member in tar.getmembers():
                    if member.isfile():  # Only interested in files
                        f = tar.extractfile(member)
                        if f:
                            return f.read().decode('utf-8')
            return None

        if any(output_type in additional_compiler_outputs for output_type in ["llvm_ir", "asm", "mir"]):
            # Create a container to extract the files
            extractor_container = None
            try:
                extractor_container = docker_client.containers.run(
                    DOCKER_IMAGE,
                    name=f"extractor_container_{session_id}",
                    command="sleep 10",  # Give it enough time to extract files
                    volumes={
                        volume_name: {
                            "bind": "/workspace",
                            "mode": "ro"  # Read-only access is sufficient
                        }
                    },
                    working_dir="/workspace",
                    network_disabled=True,
                    detach=True,
                )
                
                # Get the base name of the entry file (without extension)
                base_name = "/workspace/artifacts/a"
                additional_outputs = {}
                
                # Extract and process each requested output type
                if "llvm_ir" in additional_compiler_outputs:
                    ir_path = f"{base_name}.ll"
                    # Get the LLVM IR file from the container
                    try:
                        # First check if file exists
                        exit_code, _ = extractor_container.exec_run(f"test -f {ir_path}")
                        if exit_code == 0:  # File exists
                            bits, _ = extractor_container.get_archive(f"{ir_path}")
                            # Extract the file content
                            file_content = extract_file_from_archive(bits)
                            if file_content:
                                additional_outputs["llvm_ir"] = file_content
                    except Exception as e:
                        print(f"Error extracting LLVM IR: {e}")
                
                if "asm" in additional_compiler_outputs:
                    asm_path = f"{base_name}.s"
                    # Get the assembly file from the container
                    try:
                        exit_code, _ = extractor_container.exec_run(f"test -f {asm_path}")
                        if exit_code == 0:  # File exists
                            bits, _ = extractor_container.get_archive(f"{asm_path}")
                            # Extract the file content
                            file_content = extract_file_from_archive(bits)
                            if file_content:
                                additional_outputs["asm"] = file_content
                    except Exception as e:
                        print(f"Error extracting assembly: {e}")
                
                if "mir" in additional_compiler_outputs:
                    mir_path = f"{base_name}.mir"
                    # Get the MIR file from the container
                    try:
                        exit_code, _ = extractor_container.exec_run(f"test -f {mir_path}")
                        if exit_code == 0:  # File exists
                            bits, _ = extractor_container.get_archive(f"{mir_path}")
                            # Extract the file content
                            file_content = extract_file_from_archive(bits)
                            if file_content:
                                additional_outputs["mir"] = file_content
                    except Exception as e:
                        print(f"Error extracting MIR: {e}")
                
                # Add the additional outputs to the results
                if additional_outputs:
                    results["additional_outputs"] = additional_outputs
                    
            finally:
                # Ensure the container is removed after use
                if extractor_container and delete_containers_on_exit:
                    extractor_container.remove(force=True)


        return results

@mcp.tool()
def build_and_run(entry, opt_level="2", compile_args=[], run_args=[], input=None,
                       build_env_vars={}, run_env_vars={}, additional_compiler_outputs=[],
                       delete_volumes_on_exit=True, delete_containers_on_exit=True) -> Dict:
    """
    Compiles the contents of the current workspace inside a docker container using `rustc`, and then
    runs the compiled binary with the provided arguments and input. Returns the logs, exit code, and metrics for the build and run.

    NOTE: The sandbox does NOT support any external libraries or modules.

    Args:
        entry (str): The path to the Rust source file to compile, relative to the workspace root.
        opt_level (str): The optimization level to use for the Rust compiler. Possible levels are 0-3, s, or z (default: "2")
        additional_compiler_outputs (List[str]): Additional compiler outputs to emit. Possible values are "llvm_ir", "asm", "mir".
        compile_args (List[str]): Arguments to pass to the Rust compiler.
        run_args (List[str]): Arguments to pass to the compiled binary.
        input (str): Input to pass to the compiled binary.
        build_env_vars (Dict[str, str]): Environment variables to set for the build container.
        run_env_vars (Dict[str, str]): Environment variables to set for the run container.
        delete_volumes_on_exit (bool): Whether to delete the volumes after use.
        delete_containers_on_exit (bool): Whether to delete the containers after use.
    """
    return build_and_run_code(entry, opt_level, compile_args, run_args, input,
                              build_env_vars, run_env_vars, additional_compiler_outputs,
                              delete_volumes_on_exit, delete_containers_on_exit)
@mcp.tool()
def get_asm(entry, opt_level="2", compile_args=[], run_args=[], input=None,
                       build_env_vars={}, run_env_vars={},
                       delete_volumes_on_exit=False, delete_containers_on_exit=True) -> Dict:
    """
    Compiles the contents of the current workspace inside a docker container using `rustc`, and then
    returns the llvm_ir, asm, mir outputs.

    NOTE: The sandbox does NOT support any external libraries or modules.

    Args:
        entry (str): The path to the Rust source file to compile, relative to the workspace root.
        opt_level (str): The optimization level to use for the Rust compiler. Possible levels are 0-3, s, or z (default: "2")
        compile_args (List[str]): Arguments to pass to the Rust compiler.
        run_args (List[str]): Arguments to pass to the compiled binary.
        input (str): Input to pass to the compiled binary.
        build_env_vars (Dict[str, str]): Environment variables to set for the build container.
        run_env_vars (Dict[str, str]): Environment variables to set for the run container.
        delete_volumes_on_exit (bool): Whether to delete the volumes after use.
        delete_containers_on_exit (bool): Whether to delete the containers after use.
    """
    return build_and_run_code(entry, opt_level, compile_args, run_args, input,
                              build_env_vars, run_env_vars, ["llvm_ir", "asm", "mir"],
                              delete_volumes_on_exit, delete_containers_on_exit, only_get_asm=True)

                       