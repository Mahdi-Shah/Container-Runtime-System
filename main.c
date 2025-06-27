#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <sched.h>
#include <unistd.h>
#include <sys/wait.h>
#include <string.h>
#include <sys/mount.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <errno.h>
#include <sys/sysmacros.h>
#include <signal.h> 

#define STACK_SIZE (1024 * 1024)
#define CGROUP_BASE "/sys/fs/cgroup/my-container-manager"
#define TEMP_BASE "/tmp"

typedef struct {
    char* hostname;
    char* rootfs_path;
    char* memory_limit;
    char* cpu_quota_str;
    char** cmd_argv;
    char merged_path[256];
    char cgroup_path[256];
    pid_t parent_pid;
} container_config;

struct child_args {
    int pipe_fd[2];
    container_config* config;
};

void die(const char* message) {
    perror(message);
    exit(EXIT_FAILURE);
}

// این تابع مثل قبل باقی می‌ماند برای فایل‌هایی که نیاز به بازنویسی کامل دارند
void write_file(const char* path, const char* value) {
    int fd = open(path, O_WRONLY | O_CREAT, 0644);
    if (fd == -1) {
        fprintf(stderr, "ERROR: Failed to open %s: ", path);
        die("open");
    }
    if (write(fd, value, strlen(value)) == -1) {
        fprintf(stderr, "ERROR: Failed to write to %s: ", path);
        die("write");
    }
    close(fd);
}

void sigterm_handler(int signum) {
    exit(0);
}


void prepare_overlayfs(container_config* config) {
    char upper_path[256], work_path[256], overlay_opts[512];
    
    snprintf(config->merged_path, sizeof(config->merged_path), "%s/%s-merged", TEMP_BASE, config->hostname);
    snprintf(upper_path, sizeof(upper_path), "%s/%s-upper", TEMP_BASE, config->hostname);
    snprintf(work_path, sizeof(work_path), "%s/%s-work", TEMP_BASE, config->hostname);
    
    if (mkdir(config->merged_path, 0755) == -1 && errno != EEXIST) die("mkdir merged_path");
    if (mkdir(upper_path, 0755) == -1 && errno != EEXIST) die("mkdir upper_path");
    if (mkdir(work_path, 0755) == -1 && errno != EEXIST) die("mkdir work_path");

    snprintf(overlay_opts, sizeof(overlay_opts), "lowerdir=%s,upperdir=%s,workdir=%s", config->rootfs_path, upper_path, work_path);

    fprintf(stderr, "==> EXECUTOR: Mounting overlayfs with options: %s\n", overlay_opts);
    if (mount("overlay", config->merged_path, "overlay", 0, overlay_opts) == -1) {
        die("mount overlayfs");
    }
}

// آماده‌سازی Cgroups
void setup_cgroups(pid_t child_pid, container_config* config) {
    snprintf(config->cgroup_path, sizeof(config->cgroup_path), "%s/%d", CGROUP_BASE, child_pid);
    if (mkdir(config->cgroup_path, 0755) == -1 && errno != EEXIST) {
        fprintf(stderr, "WARNING: mkdir cgroup_path for child %d: ", child_pid);
        perror("");
    }

    char file_path[256], value_str[256];
    
    // افزودن فرزند به cgroup
    snprintf(file_path, sizeof(file_path), "%s/cgroup.procs", config->cgroup_path);
    snprintf(value_str, sizeof(value_str), "%d", child_pid);
    write_file(file_path, value_str);

    // اعمال محدودیت حافظه
    if (strcmp(config->memory_limit, "none") != 0) {
        snprintf(file_path, sizeof(file_path), "%s/memory.max", config->cgroup_path);
        write_file(file_path, config->memory_limit);
    }
    // اعمال محدودیت پردازنده
    if (strcmp(config->cpu_quota_str, "none") != 0) {
        snprintf(value_str, sizeof(value_str), "%s 100000", config->cpu_quota_str);
        snprintf(file_path, sizeof(file_path), "%s/cpu.max", config->cgroup_path);
        write_file(file_path, value_str);
    }
}

// آماده‌سازی User Namespace
void setup_userns_mappings(pid_t child_pid) {
    char file_path[256], value_str[256];
    
    snprintf(file_path, sizeof(file_path), "/proc/%d/setgroups", child_pid);
    write_file(file_path, "deny");

    snprintf(file_path, sizeof(file_path), "/proc/%d/gid_map", child_pid);
    snprintf(value_str, sizeof(value_str), "0 %d 1", getgid());
    write_file(file_path, value_str);

    snprintf(file_path, sizeof(file_path), "/proc/%d/uid_map", child_pid);
    snprintf(value_str, sizeof(value_str), "0 %d 1", getuid());
    write_file(file_path, value_str);
}


// --- تابع اصلی فرزند ---
static int child_main(void *arg) {
    struct child_args *args = (struct child_args *)arg;
    container_config* config = args->config;
    char sync_byte;
    
    close(args->pipe_fd[1]);
    
    fprintf(stderr, "==> CHILD: Waiting for parent setup...\n");
    if (read(args->pipe_fd[0], &sync_byte, 1) != 1) { 
        die("CHILD: pipe read for sync"); 
    }
    close(args->pipe_fd[0]);
    fprintf(stderr, "==> CHILD: Setup complete, finalizing environment.\n");

    if (mount(NULL, "/", NULL, MS_PRIVATE | MS_REC, NULL) == -1) die("CHILD: mount MS_PRIVATE");
    if (chroot(config->merged_path) == -1) {
        fprintf(stderr, "CHILD ERROR: chroot to '%s' failed: ", config->merged_path);
        die("chroot");
    }
    if (chdir("/") == -1) die("CHILD: chdir");
    if (mount("proc", "/proc", "proc", 0, NULL) == -1) die("CHILD: mount /proc");
    if (sethostname(config->hostname, strlen(config->hostname)) == -1) die("CHILD: sethostname");
    
    if (signal(SIGTERM, sigterm_handler) == SIG_ERR) {
        die("CHILD: Failed to register signal handler");
    }
    
    fprintf(stderr, "==> CHILD: Container is running. Waiting for signals...\n");
    fprintf(stderr, "###\n");
    
    while(1) {
        pause();
    }
    
    return 0;
}


// --- تابع اصلی والد (اجراکننده) ---
int main(int argc, char *argv[]) {
    if (argc < 5) {
        fprintf(stderr, "EXECUTOR USAGE: <hostname> <rootfs> <mem_limit> <cpu_quota> <read_bps> <write_bps> <container_dir> [ignored_cmd...]\n");
        return 1;
    }
    
    container_config config;
    config.parent_pid = getpid();
    config.hostname = argv[1];
    config.rootfs_path = argv[2];
    config.memory_limit = argv[3];
    config.cpu_quota_str = argv[4];
    config.cmd_argv = &argv[5];

    struct stat rootfs_stat;
    if (stat(config.rootfs_path, &rootfs_stat) == -1) {
        die("stat rootfs_path failed");
    }
    
    prepare_overlayfs(&config);
    
    mkdir(CGROUP_BASE, 0755);
    char subtree_control_path[256];
    snprintf(subtree_control_path, sizeof(subtree_control_path), "%s/cgroup.subtree_control", CGROUP_BASE);
    write_file(subtree_control_path, "+cpu +memory +io");

    struct child_args args;
    args.config = &config;
    if (pipe(args.pipe_fd) == -1) die("pipe");
    char *stack = malloc(STACK_SIZE);
    char *stack_top = stack + STACK_SIZE;
    if (!stack) die("malloc");
    
    int clone_flags = CLONE_NEWUTS | CLONE_NEWPID | CLONE_NEWNS | CLONE_NEWUSER | CLONE_NEWNET;
    pid_t child_pid = clone(child_main, stack_top, clone_flags | SIGCHLD, &args);
    if (child_pid == -1) die("clone");
    fprintf(stderr, "==> EXECUTOR: Created child with PID %d\n", child_pid);
    
    setup_cgroups(child_pid, &config);
    setup_userns_mappings(child_pid);
    
    close(args.pipe_fd[0]);
    if (write(args.pipe_fd[1], "", 1) != 1) die("write to pipe");
    close(args.pipe_fd[1]);

    fprintf(stderr, "==> EXECUTOR: Parent process is exiting. Child container remains active.\n");
    
    return 0;
}
