import click
import subprocess
import uuid
import json
import os
import traceback
import signal
import shutil
import re
import sys
from pathlib import Path

# Base path for storing all container information
CONTAINER_BASE_DIR = Path("/var/lib/my-container-manager")
# Path to the C executor engine
EXECUTOR_PATH = Path(__file__).parent.resolve() / "container_executor"
NS_ENTER_PATH = Path(__file__).parent.resolve() / "ns_enter" # مسیر ابزار جدید برای exec
# Path to Cgroup hierarchy
CGROUP_BASE = Path("/sys/fs/cgroup/my-container-manager")
# Base for temporary overlayfs directories
TEMP_BASE = Path("/tmp")

# --- Helper Functions ---

def get_container_dir(container_id_prefix):
    """Finds a container directory by a unique prefix of its ID."""
    if not container_id_prefix:
        return None
    candidates = []
    for d in CONTAINER_BASE_DIR.iterdir():
        if d.is_dir() and d.name.startswith(container_id_prefix):
            candidates.append(d)
    if len(candidates) == 1:
        return candidates[0]
    elif len(candidates) > 1:
        click.echo(f"Error: Ambiguous container ID prefix '{container_id_prefix}'.", err=True)
    else:
        click.echo(f"Error: No such container: '{container_id_prefix}'.", err=True)
    return None

def get_container_config(container_dir):
    """Reads and returns the container's config.json."""
    config_path = container_dir / "config.json"
    if not config_path.exists():
        return None
    with open(config_path, "r") as f:
        return json.load(f)

def update_container_status(container_dir, new_status, new_pid=None):
    """Updates the status and optionally the PID in the container's config.json."""
    config = get_container_config(container_dir)
    if not config: return
    config["status"] = new_status
    if new_pid is not None:
        config["pid"] = new_pid
    with open(container_dir / "config.json", "w") as f:
        json.dump(config, f, indent=4)

# --- CLI Command Group ---

@click.group()
def cli():
    """A simple tool to manage containers, written in Python and C."""
    # This might need sudo if the parent dir is owned by root
    if not CONTAINER_BASE_DIR.exists():
        try:
            CONTAINER_BASE_DIR.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            click.echo(f"Error: Cannot create {CONTAINER_BASE_DIR}. Please run with sudo or create it manually.", err=True)
            sys.exit(1)
    pass

# --- CLI Commands ---

@cli.command()
@click.option("--memory", default="none", help="Memory limit (e.g., 100M).")
@click.option("--cpu", default=None, type=float, help="CPU quota (e.g., 0.5 for 50%).")
@click.argument("rootfs_path", type=click.Path(exists=True, file_okay=False, resolve_path=True))
def run(memory, cpu, rootfs_path):
    """Runs a new container."""
    
    container_id = str(uuid.uuid4())[:12]
    click.echo(f"==> MANAGER: Creating container with ID: {container_id}")
    container_dir = CONTAINER_BASE_DIR / container_id
    container_dir.mkdir(parents=True, mode=0o777, exist_ok=True)

    cpu_quota = "none"
    if cpu is not None:
        cpu_quota = str(int(cpu * 100000))

    hostname = f"cont-{container_id}"
    config = {
        "id": container_id,
        "hostname": hostname, 
        "rootfs": rootfs_path,
        "memory_limit": memory,
        "cpu_quota": cpu_quota,
        "status": "creating",
        "pid": None
    }
    
    config_path = container_dir / "config.json"
    with open(config_path, "w") as f:
        json.dump(config, f, indent=4)
        
    executor_args = [
        "sudo", str(EXECUTOR_PATH), hostname, 
        rootfs_path, memory, cpu_quota
    ]

    click.echo(f"==> MANAGER: Invoking C executor...")
    
    # ... (بقیه تابع run بدون تغییر) ...
    process = None 
    child_pid = None 

    try:
        process = subprocess.Popen(
            executor_args,
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE, 
            text=True,
            encoding='utf-8'
        )

        for line in process.stderr:
            click.echo(line, nl=False)
            match = re.search(r"Created child with PID (\d+)", line)
            if match:
                child_pid = int(match.group(1))
                config["pid"] = child_pid
            if "Parent process is exiting" in line:
                break

        return_code = process.wait(timeout=2)
                
        if return_code == 0 and child_pid is not None:
            config["status"] = "running"
            click.echo(f"\n==> MANAGER: Container started successfully in the background. PID: {child_pid}")
        else:
            config["status"] = "failed"
            click.echo(f"\nCRITICAL ERROR: C executor parent process failed with exit code {return_code}.", err=True)

    except subprocess.CalledProcessError as e:
        config["status"] = "failed"
        click.echo(f"CRITICAL ERROR: C executor failed with exit code {e.returncode}.", err=True)
        click.echo("--- C Executor stderr ---", err=True)
        click.echo(e.stderr if e.stderr else "<empty>", err=True)
    finally:
        update_container_status(container_dir, config["status"])
        with open(config_path, "w") as f:
            json.dump(config, f, indent=4)
        click.echo(f"==> MANAGER: Final container status: '{config['status']}'.")

@cli.command()
@click.argument("container_id")
def start(container_id):
    """Starts a stopped container."""
    container_dir = get_container_dir(container_id)
    if not container_dir: return

    config = get_container_config(container_dir)
    if not config: return

    if config['status'] == 'running':
        click.echo(f"Error: Container '{container_id}' is already running.", err=True)
        return
    if config['status'] not in ['stopped', 'created', 'failed']:
         click.echo(f"Error: Cannot start container in state '{config['status']}'.", err=True)
         return

    click.echo(f"==> MANAGER: Starting container {config['id']}...")
    update_container_status(container_dir, 'starting')

    ## CHANGED ##
    executor_args = [
        "sudo", str(EXECUTOR_PATH), config['hostname'], 
        config['rootfs'], 
        config.get('memory_limit', 'none'), 
        config.get('cpu_quota', 'none')
    ]

    process, new_child_pid = None, None
    try:
        process = subprocess.Popen(
            executor_args, stderr=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, encoding='utf-8'
        )
        for line in process.stderr:
            click.echo(line, nl=False)
            match = re.search(r"Created child with PID (\d+)", line)
            if match:
                new_child_pid = int(match.group(1))
            if "Parent process is exiting" in line:
                break
        
        process.wait(timeout=2)
        if process.returncode == 0 and new_child_pid is not None:
            update_container_status(container_dir, 'running', new_child_pid)
            click.echo(f"\n==> MANAGER: Container started successfully. New PID: {new_child_pid}")
        else:
            update_container_status(container_dir, 'failed')
            err_output = process.stderr.read()
            click.echo(f"\nCRITICAL ERROR: C executor failed. RC: {process.returncode}\n{err_output}", err=True)

    except Exception as e:
        update_container_status(container_dir, 'failed')
        click.echo(f"\nCRITICAL ERROR: An unexpected error occurred: {e}", err=True)
        traceback.print_exc(file=sys.stderr)
    finally:
        click.echo(f"==> MANAGER: Container '{config['id']}' start sequence complete.")




@cli.command(name="list")
def list_containers():
    """Lists all created containers."""
    click.echo(f"{'CONTAINER ID':<15} {'STATUS':<10} {'PID':<10}")
    click.echo("-" * 35)
    for d in sorted(CONTAINER_BASE_DIR.iterdir()):
        if d.is_dir():
            config = get_container_config(d)
            if not config:
                continue
            
            status = config.get("status", "unknown")
            pid = config.get("pid")
            
            # Check if the process is actually running
            if pid and status not in ["exited", "stopped", "failed"]:
                if os.path.exists(f"/proc/{pid}"):
                    status = "running"
                else:
                    status = "stopped"
                    update_container_status(d, status)
            
            click.echo(f"{config['id']:<15} {status:<10} {pid if pid else 'N/A':<10}")

@cli.command()
@click.argument("container_id")
@click.argument("command", required=True, nargs=-1)
def exec(container_id, command):
    """یک دستور جدید را داخل یک کانتینر در حال اجرا، اجرا می‌کند."""
    container_dir = get_container_dir(container_id)
    if not container_dir: return
    
    config = get_container_config(container_dir)
    if not config: return
    
    pid = config.get("pid")
    hostname = config.get("hostname")

    if not pid or not os.path.exists(f"/proc/{pid}"):
        click.echo(f"Error: Container '{config.get('id', container_id)}' is not running.", err=True)
        return

    # [FIXED] Construct the correct path to the merged directory in /tmp
    if not hostname:
        click.echo(f"Error: 'hostname' not found in container's config file.", err=True)
        return

    merged_path = TEMP_BASE / f"{hostname}-merged"
    if not merged_path.exists():
        click.echo(f"Error: Container root path '{merged_path}' not found. Has the container been started correctly?", err=True)
        return

    ns_enter_args = [
        "sudo",
        str(NS_ENTER_PATH),
        str(pid),
        str(merged_path), # Pass the correct path
        *command
    ]
    
    click.echo(f"==> MANAGER: Entering container '{container_id}' (PID: {pid}) to run: {' '.join(command)}")
    click.echo("--- Attaching to container ---")
    
    subprocess.run(ns_enter_args)
    
    click.echo("\n--- Detached from container ---")


@cli.command()
@click.argument("container_id")
def status(container_id):
    """Shows the status and resource usage of a container."""
    container_dir = get_container_dir(container_id)
    if not container_dir: return
    
    config = get_container_config(container_dir)
    pid = config.get("pid")
    
    if not pid or not os.path.exists(f"/proc/{pid}"):
        click.echo(f"Container '{config['id']}' is not running.")
        return

    click.echo(f"--- Status for Container: {config['id']} ---")
    click.echo(f"Status: {config['status']}")
    click.echo(f"PID: {pid}")
    
    try:
        with open(f"{CGROUP_BASE}/{pid}/memory.current") as f:
            mem_current = int(f.read().strip())
            click.echo(f"Memory Usage: {mem_current / 1024 / 1024:.2f} MB")
        with open(f"{CGROUP_BASE}/{pid}/memory.max") as f:
            mem_max = f.read().strip()
            click.echo(f"Memory Limit: {mem_max}")
        with open(f"{CGROUP_BASE}/{pid}/cpu.stat") as f:
            cpu_stat = f.read().strip()
            click.echo(f"CPU Stats:\n{cpu_stat}")
    except FileNotFoundError:
        click.echo("Could not retrieve Cgroup stats. Is the container running?")
    except Exception as e:
        click.echo(f"An error occurred while reading Cgroup stats: {e}", err=True)


@cli.command()
@click.argument("container_id")
def stop(container_id):
    """Stops a running container by sending SIGTERM."""
    container_dir = get_container_dir(container_id)
    if not container_dir: return

    config = get_container_config(container_dir)
    if not config: return
    
    pid = config.get("pid")
    if not pid:
        click.echo("Error: Container has no PID.", err=True)
        update_container_status(container_dir, "stopped")
        return

    # Check if process exists
    if not os.path.exists(f"/proc/{pid}"):
        click.echo(f"Container '{config['id']}' is already stopped.", err=True)
        update_container_status(container_dir, "stopped")
        return

    click.echo(f"Stopping container {config['id']} (PID: {pid})...")
    try:
        # Send SIGTERM for graceful shutdown
        # This requires permissions, so this command might need to be run with sudo
        os.kill(pid, signal.SIGTERM)
        click.echo("SIGTERM signal sent.")
        update_container_status(container_dir, "stopped")
    except ProcessLookupError:
        click.echo("Process already exited.", err=True)
        update_container_status(container_dir, "stopped")
    except PermissionError:
        click.echo("Error: Permission denied to send signal. Try running with 'sudo'.", err=True)
    except Exception as e:
        click.echo(f"Failed to stop container: {e}", err=True)
        traceback.print_exc()

@cli.command()
@click.argument("container_id")
def rm(container_id):
    """Removes a stopped container and cleans up all its resources."""
    container_dir = get_container_dir(container_id)
    if not container_dir: return

    config = get_container_config(container_dir)
    if not config: return
    
    status = config.get("status", "unknown")
    pid = config.get("pid")

    if pid and os.path.exists(f"/proc/{pid}"):
        click.echo("Error: Cannot remove a running container. Please stop it first with 'stop' command.", err=True)
        return

    click.echo(f"Removing container {config['id']}...")
    try:
        # All cleanup operations likely require sudo
        
        # 1. Cleanup Cgroup directory
        if pid:
            cgroup_path = CGROUP_BASE / str(pid)
            if cgroup_path.exists():
                click.echo(f"Removing Cgroup directory: {cgroup_path}")
                subprocess.run(["sudo", "rmdir", str(cgroup_path)], check=False)

        # 2. Cleanup OverlayFS directories from /tmp
        hostname = config.get("hostname")
        if hostname:
            merged_path = TEMP_BASE / f"{hostname}-merged"
            upper_path = TEMP_BASE / f"{hostname}-upper"
            work_path = TEMP_BASE / f"{hostname}-work"
            
            click.echo(f"Unmounting {merged_path}...")
            subprocess.run(["sudo", "umount", str(merged_path)])
            
            click.echo("Removing OverlayFS directories...")
            if upper_path.exists(): subprocess.run(["sudo", "rm", "-rf", str(upper_path)])
            if work_path.exists(): subprocess.run(["sudo", "rm", "-rf", str(work_path)])

            # i dont no why but if delete this then cant remove merged
            subprocess.run(["sudo", "umount", str(merged_path)])
            if merged_path.exists(): subprocess.run(["sudo", "rm", "-rf", str(merged_path)])

        # 3. Cleanup permanent storage directory
        click.echo(f"Removing container data: {container_dir}")
        subprocess.run(["sudo", "rm", "-rf", str(container_dir)])
        
        click.echo(f"Container {container_id} removed successfully.")
    except Exception as e:
        click.echo(f"Failed to remove container. Error: {e}", err=True)
        traceback.print_exc()


if __name__ == "__main__":
    cli()
