# My Container Manager

A lightweight, educational container manager built from scratch in Python and C. This project demonstrates the core principles of Linux containerization by using fundamental kernel features like namespaces, cgroups, and OverlayFS.

## About The Project

This project is a simplified imitation of tools like Docker, designed to provide a hands-on understanding of how containers work under the hood. It is not intended for production use but serves as an excellent learning tool for operating systems concepts.

The architecture is split into two main parts:
* A user-friendly **Command-Line Interface (CLI)** written in Python for managing containers.
* Low-level **Executor Programs** written in C that interact directly with the Linux kernel to create and manage isolated environments.

## Features

* **Create & Run Containers**: Launch new containers from a specified root filesystem.
* **Filesystem Isolation**: Uses **OverlayFS** for a copy-on-write filesystem, keeping the base image immutable.
* **Resource Limiting**: Apply memory and CPU usage limits using **Cgroups v2**.
* **Process & Network Isolation**: Utilizes Linux **Namespaces** (PID, Mount, UTS, Network, etc.) to create isolated environments.
* **Container Lifecycle Management**: `run`, `start`, `stop`, and `rm` containers.
* **List Containers**: View all created containers and their current status (`running`, `stopped`).
* **Execute Commands**: Attach to a running container and execute commands inside it (`exec` functionality).
* **Inspect Usage**: Check the real-time resource consumption of a container.

## How It Works

The project consists of three key components that work together:

### 1. The container_cli (`container_cli.py`)
This is the high-level controller and user interface.
* Built with the `click` library in Python.
* Parses user commands (e.g., `run`, `list`, `exec`).
* Manages container metadata by storing configuration details (ID, status, PID) in JSON files located at `/var/lib/my-container-manager/`.
* It orchestrates the container creation and execution by invoking the C programs with the appropriate arguments and `sudo` privileges.

### 2. The Container Executor (`container_executor.c`)
This is the core engine responsible for creating a new container.
* It is called by the Python manager when a user wants to `run` or `start` a container.
* It uses the `clone()` system call with namespace flags (`CLONE_NEWPID`, `CLONE_NEWNS`, `CLONE_NEWUTS`, etc.) to spawn a new process in an isolated environment.
* **Environment Setup**:
    * Creates and mounts an **OverlayFS** to provide a writable layer on top of the read-only rootfs.
    * Creates a new Cgroup under `/sys/fs/cgroup/my-container-manager/` and applies the user-specified memory/CPU limits.
    * Maps the user and group IDs for the new User namespace.
* **Daemonization**: After setting up the environment, the parent process exits, leaving the child process (the container) running in the background. The child process waits indefinitely using `pause()`, keeping the container "alive" until it receives a signal (e.g., `SIGTERM` from the `stop` command).

### 3. The Namespace Enter Tool (`ns_enter.c`)
This utility provides the functionality for the `exec` command.
* It takes a container's PID, its root path, and a command to execute.
* It uses the `setns()` system call to join the existing namespaces (Mount, PID, Network, etc.) of the target container process.
* After joining the mount namespace, it performs a `chroot()` to confine itself to the container's filesystem.
* Finally, it uses `execvp()` to replace its own process with the command specified by the user, which now runs fully inside the container's context.

## Core Concepts Implemented

This project is a practical demonstration of the following Linux kernel features:
* **Namespaces**: PID, Mount (mnt), UTS, IPC, Network (net), and User.
* **Control Groups (Cgroups) v2**: For resource accounting and limitation (`memory.max`, `cpu.max`).
* **OverlayFS**: A union mount filesystem for creating copy-on-write layers.
* **System Calls**: `clone()`, `setns()`, `chroot()`, `mount()`, `unshare()`.

## Getting Started

### Prerequisites

* A Linux-based operating system.
* `root` or `sudo` privileges.
* Python 3 and `pip`.
* A C compiler, such as `gcc`.

### Installation & Build

1.  **Clone the repository:**
    ```sh
    git clone https://github.com/Mahdi-Shah/Container-Runtime-System.git
    ```

2.  **Compile the C executables:**
    The Python script expects the compiled binaries to be in the same directory.
    ```sh
    gcc -o container_executor container_executor.c
    gcc -o ns_enter ns_enter.c
    ```

### Preparing a Root Filesystem

You need a root filesystem (`rootfs`) for your container to run. You can create a minimal Debian system using `debootstrap`.

```sh
docker pull alpine
docker create --name temp-alpine alpine
mkdir my-alpine-rootfs
docker export temp-alpine | tar -x -C my-alpine-rootfs
docker rm temp-alpine
```

## Usage

**Note**: Most commands require `sudo` because they perform privileged operations like creating namespaces and managing system resources.

1.  **Run a new container:**
    ```sh
    sudo python3 container_cli.py run --memory 100M --cpu 0.5 /bin/sh
    ```
    * `--memory`: Memory limit (e.g., 512M, 1G).
    * `--cpu`: CPU quota (e.g., 0.5 for 50% of one core).

2.  **List all containers:**
    ```sh
    sudo python3 container_cli.py list
    ```

    **Sample Output:**

    ```
    CONTAINER ID    STATUS     PID       
    -----------------------------------
    a6213154-c4f    running    9400      
    f1e3901e-669    stopped    9433      
    ```

3.  **Execute a command inside a running container:**
    Get a shell inside the container:
    ```sh
    sudo python3 container_cli.py exec <container-id> /bin/sh
    ```

4.  **Check the status and resource usage of a container:**
    ```sh
    sudo python3 container_cli.py status <container-id>
    ```

5.  **Stop a running container:**
    ```sh
    sudo python3 container_cli.py stop <container-id>
    ```

6.  **Start a stopped container:**
    ```sh
    sudo python3 container_cli.py start <container-id>
    ```

7.  **Remove a stopped container:**
    This will clean up all associated resources (cgroups, OverlayFS directories, and config files).
    ```sh
    sudo python3 container_cli.py rm <container-id>
    ```
