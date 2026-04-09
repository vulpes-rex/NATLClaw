"""Test suite for secure shell operations and command whitelist."""
from __future__ import annotations

import os
import shlex
import subprocess
import pytest
import sys
from unittest.mock import patch, MagicMock
# Mock external dependencies
with patch.dict('sys.modules', {
    'agent_framework_github_copilot': MagicMock(),
    'agent_framework': MagicMock(),
    'agent_framework.foundry': MagicMock(),
    'agent_framework.openai': MagicMock(),
    'agent_framework.ollama': MagicMock(),
    'azure.identity': MagicMock(),
    'copilot': MagicMock(),
}):
    from personas.devops_engineer.tools import validate_and_execute_command as devops_validate_and_execute
    from personas.python_developer.tools import validate_and_execute_command as python_validate_and_execute
    from personas.react_developer.tools import validate_and_execute_command as react_validate_and_execute

# Common test data
ALLOWED_COMMANDS = {
    'ls': ['-l', '-a', '-h', '-r', '-t', '-R', '-F', '-1'],
    'cat': ['-n', '-b', '-E', '-T'],
    'grep': ['-i', '-v', '-r', '-n', '-c', '-l', '-H', '-h', '-A', '-B', '-C'],
    'find': ['-name', '-type', '-mtime', '-size', '-user', '-group'],
    'pwd': [],
    'echo': [],
    'mkdir': ['-p', '-m'],
    'rmdir': ['-p'],
    'cp': ['-r', '-f', '-p', '-v'],
    'mv': ['-f', '-i', '-v'],
    'touch': [],
    'head': ['-n', '-c'],
    'tail': ['-n', '-c', '-f'],
    'sort': ['-n', '-r', '-u'],
    'uniq': ['-c', '-d', '-u'],
    'wc': ['-l', '-w', '-c'],
    'date': ['-u', '+%Y-%m-%d', '+%H:%M:%S'],
    'whoami': [],
    'uname': ['-a', '-r', '-n', '-m'],
    'ping': ['-c', '-n', '-q', '-i'],
    'curl': ['-s', '-f', '-o', '-I', '-X'],
    'wget': ['-q', '-O', '-S', '-T'],
    'npm': ['run', 'test', 'build', 'install', '--save', '--save-dev', '--global'],
    'pytest': ['-x', '-v', '-k', '--cov', '--junitxml', '--help'],
    'python': ['-m', '--version'],
    'pip': ['install', 'freeze', 'list'],
    'node': ['--version', '-e'],
    'npx': ['--version', 'test'],
    'git': ['--version', 'status', 'pull', 'push', 'clone', 'commit', 'add', 'branch'],
}

DISALLOWED_COMMANDS = [
    'rm', 'dd', 'ddrescue', 'shred', 'mkfs', 'fdisk', 'diskutil',
    'sudo', 'su', 'chmod', 'chown', 'chgrp', 'mount', 'umount',
    'systemd', 'systemctl', 'service', 'init', 'telinit',
    'halt', 'poweroff', 'reboot', 'shutdown',
    'passwd', 'chsh', 'chfn', 'usermod', 'useradd', 'userdel',
    'groupadd', 'groupdel', 'gpasswd', 'newgrp',
    'nmap', 'tcpdump', 'wireshark', 'tshark', 'ettercap',
    'ssh', 'scp', 'sftp', 'rsync', 'rclone',
    'mysql', 'psql', 'sqlite3', 'pgadmin', 'phpmyadmin',
    'docker', 'podman', 'buildah', 'containerd', 'runc',
    'kubectl', 'minikube', 'kind', 'k3s', 'microk8s',
    'helm', 'tiller', 'istio', 'linkerd', 'envoy',
    'aws', 'gcloud', 'az', 'ibmcloud', 'openstack',
    'terraform', 'ansible', 'chef', 'puppet', 'salt',
    'jenkins', 'gitlab', 'github', 'bitbucket', 'travis',
    'circleci', 'codeship', 'drone', 'semaphore',
    'vagrant', 'virtualbox', 'vmware', 'parallels', 'qemu',
    'kvm', 'libvirt', 'xen', 'hyperv', 'proxmox',
    'sensu', 'zabbix', 'prometheus', 'grafana', 'kibana',
    'logstash', 'elasticsearch', 'splunk', 'graylog',
    'postfix', 'sendmail', 'exim', 'qmail', 'nullmailer',
    'dovecot', 'postfix', 'exim', 'qmail', 'sendmail',
    'apache', 'nginx', 'lighttpd', 'iis', 'caddy',
    'mysql', 'postgresql', 'mongodb', 'redis', 'memcached',
    'rabbitmq', 'kafka', 'celery', 'activemq', 'nats',
    'varnish', 'squid', 'nginx', 'haproxy', 'envoy',
    'consul', 'etcd', 'zookeeper', 'kafka', 'nacos',
    'vault', 'nomad', 'consul', 'fabio', 'traefik',
    'jenkins', 'gitlab', 'github', 'bitbucket', 'travis',
    'circleci', 'codeship', 'drone', 'semaphore',
    'packer', 'chef', 'puppet', 'ansible', 'salt',
    'knife', 'test-kitchen', 'serverspec', 'infrataster',
    'serverspec', 'rspec', 'minitest', 'test_unit',
    'cucumber', 'behat', 'phpspec', 'mink',
    'phpunit', 'atoum', 'codeception', 'robo',
    'percol', 'peco', 'fzf', 'fzy', 'bumper',
    'xargs', 'parallel', 'make', 'cmake', 'autoconf',
    'automake', 'libtool', 'pkg-config', 'dpkg',
    'rpm', 'yum', 'apt', 'aptitude', 'dpkg-deb',
    'alien', 'checkinstall', 'dh_make', 'debuild',
    'dpkg-buildpackage', 'dpkg-source', 'dpkg-shlibdeps',
    'dpkg-genchanges', 'dpkg-gencontrol', 'dpkg-distaddfile',
    'dpkg-parsechangelog', 'dpkg-genchanges',
    'dpkg-buildflags', 'dpkg-architecture', 'dpkg-shlibdeps',
    'dpkg-gensymbols', 'dpkg-genchanges',
    'dpkg-distaddfile', 'dpkg-parsechangelog',
    'dpkg-buildpackage', 'dpkg-source', 'dpkg-shlibdeps',
    'dpkg-genchanges', 'dpkg-gencontrol',
    'dpkg-distaddfile', 'dpkg-parsechangelog',
    'dpkg-buildflags', 'dpkg-architecture',
    'dpkg-shlibdeps', 'dpkg-gensymbols',
    'dpkg-genchanges', 'dpkg-distaddfile',
    'dpkg-parsechangelog', 'dpkg-buildpackage',
    'dpkg-source', 'dpkg-shlibdeps', 'dpkg-genchanges',
    'dpkg-gencontrol', 'dpkg-distaddfile',
    'dpkg-parsechangelog', 'dpkg-buildflags',
    'dpkg-architecture', 'dpkg-shlibdeps',
    'dpkg-gensymbols', 'dpkg-genchanges',
    'dpkg-distaddfile', 'dpkg-parsechangelog',
    'dpkg-buildpackage', 'dpkg-source',
    'dpkg-shlibdeps', 'dpkg-genchanges',
    'dpkg-gencontrol', 'dpkg-distaddfile',
    'dpkg-parsechangelog', 'dpkg-buildflags',
    'dpkg-architecture', 'dpkg-shlibdeps',
    'dpkg-gensymbols', 'dpkg-genchanges',
    'dpkg-distaddfile', 'dpkg-parsechangelog',
    'dpkg-buildpackage', 'dpkg-source',
    'dpkg-shlibdeps', 'dpkg-genchanges',
    'dpkg-gencontrol', 'dpkg-distaddfile',
    'dpkg-parsechangelog', 'dpkg-buildflags',
    'dpkg-architecture', 'dpkg-shlibdeps',
    'dpkg-gensymbols', 'dpkg-genchanges',
    'dpkg-distaddfile', 'dpkg-parsechangelog',
]

VALID_COMMAND_TEST_CASES = [
    ("ls -l", True, "List files in long format"),
    ("ls -a", True, "List all files including hidden"),
    ("ls -la", True, "List all files in long format"),
    ("ls .", True, "List current directory"),
    ("ls ..", True, "List parent directory"),
    ("cat file.txt", True, "Read file contents"),
    ("cat -n file.txt", True, "Read file with line numbers"),
    ("grep -r 'pattern' .", True, "Recursive grep"),
    ("grep -i 'pattern'", True, "Case-insensitive grep"),
    ("find . -name '*.py'", True, "Find Python files"),
    ("find . -type f", True, "Find files"),
    ("mkdir -p new_folder", True, "Create directory recursively"),
    ("cp file1.txt file2.txt", True, "Copy file"),
    ("cp -r dir1 dir2", True, "Copy directory recursively"),
    ("mv file1.txt file2.txt", True, "Move/rename file"),
    ("touch new_file.txt", True, "Create empty file"),
    ("echo 'Hello World'", True, "Print message"),
    ("head -n 10 file.txt", True, "Get first 10 lines"),
    ("tail -f log.txt", True, "Follow log file"),
    ("sort file.txt", True, "Sort file contents"),
    ("uniq -c file.txt", True, "Count unique lines"),
    ("wc -l file.txt", True, "Count lines in file"),
    ("date", True, "Show current date/time"),
    ("date -u", True, "Show UTC date/time"),
    ("date +%Y-%m-%d", True, "Format date"),
    ("whoami", True, "Get current user"),
    ("uname -a", True, "Get system information"),
    ("ping -c 4 google.com", True, "Ping with 4 packets"),
    ("curl -s https://example.com", True, "Silent curl"),
    ("curl -f https://example.com", True, "Fail silently"),
    ("curl -o file.html https://example.com", True, "Download to file"),
    ("curl -I https://example.com", True, "HEAD request only"),
    ("curl -X POST https://example.com", True, "POST request"),
    ("wget -q https://example.com/file", True, "Quiet wget"),
    ("wget -O file.txt https://example.com", True, "Download to file"),
    ("wget -T 10 https://example.com", True, "Timeout after 10 seconds"),
    ("npm run test", True, "Run test script"),
    ("npm test", True, "Run tests (shorthand)"),
    ("npm build", True, "Build project"),
    ("npm install", True, "Install dependencies"),
    ("npm install --save-dev", True, "Install as dev dependency"),
    ("npm install -g", True, "Install globally"),
    ("pytest", True, "Run pytest"),
    ("pytest -x", True, "Stop on first failure"),
    ("pytest -v", True, "Verbose output"),
    ("pytest -k test_name", True, "Run specific test"),
    ("pytest --cov", True, "Coverage report"),
    ("pytest --junitxml=results.xml", True, "JUnit XML output"),
    ("python -m pytest", True, "Run pytest as module"),
    ("python --version", True, "Get Python version"),
    ("pip install requests", True, "Install package"),
    ("pip freeze", True, "List installed packages"),
    ("pip list", True, "List packages"),
    ("node --version", True, "Get Node version"),
    ("node -e 'console.log(\"test\")'", True, "Execute JS"),
    ("npx --version", True, "Get npx version"),
    ("npx test", True, "Run npx test"),
    ("git status", True, "Get git status"),
    ("git pull", True, "Pull changes"),
    ("git push", True, "Push changes"),
    ("git clone https://github.com/user/repo.git", True, "Clone repository"),
    ("git commit -m 'message'", True, "Commit changes"),
    ("git add .", True, "Add all changes"),
    ("git branch", True, "List branches"),
]

INVALID_COMMAND_TEST_CASES = [
    ("rm -rf /", False, "Remove recursively (dangerous)"),
    ("rm file.txt", False, "Remove file (not allowed)"),
    ("dd if=/dev/zero of=/dev/sda", False, "Disk wipe (dangerous)"),
    ("ddrescue", False, "Disk recovery (dangerous)"),
    ("shred file.txt", False, "Secure delete (dangerous)"),
    ("mkfs.ext4 /dev/sda1", False, "Create filesystem (dangerous)"),
    ("fdisk /dev/sda", False, "Partition table (dangerous)"),
    ("diskutil eraseDisk", False, "Erase disk (dangerous)"),
    ("sudo command", False, "Superuser (privilege escalation)"),
    ("su -", False, "Switch user (privilege escalation)"),
    ("chmod 777 file", False, "Change permissions (security risk)"),
    ("chown root file", False, "Change ownership"),
    ("mount /dev/sdb1 /mnt", False, "Mount filesystem"),
    ("umount /dev/sdb1", False, "Unmount filesystem"),
    ("systemctl start service", False, "Systemctl (system management)"),
    ("halt", False, "Halt system (system management)"),
    ("reboot", False, "Reboot system"),
    ("passwd", False, "Change password (security risk)"),
    ("useradd newuser", False, "Add user (system management)"),
    ("userdel user", False, "Delete user"),
    ("groupadd newgroup", False, "Add group"),
    ("groupdel group", False, "Delete group"),
    ("gpasswd -a user group", False, "Add user to group"),
    ("newgrp group", False, "New group"),
    ("nmap -sS 192.168.1.0/24", False, "Network scan (security risk)"),
    ("tcpdump -i eth0", False, "Packet capture"),
    ("ssh user@host", False, "SSH connection"),
    ("scp file user@host:/path", False, "SCP transfer"),
    ("sftp user@host", False, "SFTP connection"),
    ("rsync -avz /src/ /dst/", False, "Rsync (file operations)"),
    ("rclone sync /src remote:dst", False, "Rclone sync"),
    ("mysql -u root -p", False, "MySQL admin"),
    ("psql -U postgres", False, "PostgreSQL admin"),
    ("sqlite3 database.db", False, "SQLite (database)"),
    ("docker run -it ubuntu", False, "Docker container"),
    ("docker rm -f container", False, "Docker remove (dangerous)"),
    ("kubectl apply -f deployment.yaml", False, "Kubernetes"),
    ("helm install chart", False, "Helm chart"),
    ("aws s3 cp", False, "AWS CLI"),
    ("gcloud compute", False, "Google Cloud CLI"),
    ("az vm create", False, "Azure CLI"),
    ("terraform apply", False, "Terraform apply (infrastructure)"),
    ("ansible-playbook playbook.yml", False, "Ansible (infrastructure)"),
    ("packer build", False, "Packer build"),
    ("vagrant up", False, "Vagrant (virtualization)"),
    ("virtualbox", False, "VirtualBox (virtualization)"),
    ("vmware", False, "VMware (virtualization)"),
    ("vagrant destroy -f", False, "Vagrant destroy (dangerous)"),
    ("sensu agent", False, "Sensu agent (monitoring)"),
    ("zabbix_agentd", False, "Zabbix agent (monitoring)"),
    ("prometheus --config.file=...", False, "Prometheus (monitoring)"),
    ("grafana-server", False, "Grafana server (monitoring)"),
    ("kibana", False, "Kibana (monitoring)"),
    ("logstash -f config.conf", False, "Logstash (logging)"),
    ("elasticsearch", False, "Elasticsearch (search)"),
    ("splunk start", False, "Splunk (logging)"),
    ("graylog", False, "Graylog (logging)"),
    ("postfix start", False, "Postfix (mail)"),
    ("sendmail", False, "Sendmail (mail)"),
    ("exim -bd", False, "Exim (mail)"),
    ("qmail start", False, "Qmail (mail)"),
    ("dovecot", False, "Dovecot (mail)"),
    ("apache2 -k start", False, "Apache (web)"),
    ("nginx", False, "Nginx (web)"),
    ("lighttpd -f config.conf", False, "Lighttpd (web)"),
    ("iisreset", False, "IIS (web)"),
    ("caddy start", False, "Caddy (web)"),
    ("mysql.server start", False, "MySQL (database)"),
    ("pg_ctl start", False, "PostgreSQL (database)"),
    ("mongod", False, "MongoDB (database)"),
    ("redis-server", False, "Redis (cache)"),
    ("memcached", False, "Memcached (cache)"),
    ("rabbitmqctl start", False, "RabbitMQ (queue)"),
    ("kafka", False, "Kafka (queue)"),
    ("celery -A proj worker", False, "Celery (queue)"),
    ("activemq", False, "ActiveMQ (queue)"),
    ("nats-server", False, "NATS (queue)"),
    ("varnishd -a :80", False, "Varnish (cache)"),
    ("squid -f config.conf", False, "Squid (proxy)"),
    ("haproxy -f config.conf", False, "HAProxy (load balancer)"),
    ("envoy -c config.yaml", False, "Envoy (proxy)"),
    ("consul agent -server", False, "Consul (service mesh)"),
    ("etcd", False, "etcd (database)"),
    ("zookeeper", False, "Zookeeper (coordination)"),
    ("nacos", False, "Nacos (discovery)"),
    ("vault server", False, "Vault (security)"),
    ("nomad agent", False, "Nomad (orchestration)"),
    ("fabio", False, "Fabio (load balancer)"),
    ("traefik --configfile=config.toml", False, "Traefik (load balancer)"),
    ("jenkins", False, "Jenkins (CI/CD)"),
    ("gitlab-runner", False, "GitLab Runner (CI/CD)"),
    ("github-actions", False, "GitHub Actions (CI/CD)"),
    ("bitbucket-pipelines", False, "Bitbucket Pipelines (CI/CD)"),
    ("travis", False, "Travis CI (CI/CD)"),
    ("circleci", False, "CircleCI (CI/CD)"),
    ("codeship", False, "Codeship (CI/CD)"),
    ("drone", False, "Drone CI (CI/CD)"),
    ("semaphore", False, "Semaphore (CI/CD)"),
    ("packer build", False, "Packer (images)"),
    ("chef-client", False, "Chef (configuration)"),
    ("puppet agent", False, "Puppet (configuration)"),
    ("salt-call", False, "Salt (configuration)"),
    ("knife upload", False, "Knife (Chef)"),
    ("test-kitchen", False, "Test Kitchen (testing)"),
    ("serverspec", False, "Serverspec (testing)"),
    ("infrataster", False, "Infrataster (testing)"),
    ("rspec", False, "RSpec (testing)"),
    ("minitest", False, "Minitest (testing)"),
    ("test_unit", False, "Test::Unit (testing)"),
    ("cucumber", False, "Cucumber (testing)"),
    ("behat", False, "Behat (testing)"),
    ("phpspec", False, "Phpspec (testing)"),
    ("mink", False, "Mink (testing)"),
    ("phpunit", False, "PHPUnit (testing)"),
    ("atoum", False, "Atoum (testing)"),
    ("codeception", False, "Codeception (testing)"),
    ("robo", False, "Robo (testing)"),
    ("percol", False, "Percol (filtering)"),
    ("peco", False, "Peco (filtering)"),
    ("fzf", False, "Fzf (filtering)"),
    ("fzy", False, "Fzy (filtering)"),
    ("bumper", False, "Bumper (filtering)"),
    ("xargs", False, "Xargs (argument list)"),
    ("parallel", False, "Parallel (parallel processing)"),
    ("make", False, "Make (build)"),
    ("cmake", False, "CMake (build)"),
    ("autoconf", False, "Autoconf (build)"),
    ("automake", False, "Automake (build)"),
    ("libtool", False, "Libtool (build)"),
    ("pkg-config", False, "Pkg-config (build)"),
    ("dpkg", False, "Dpkg (package)"),
    ("rpm", False, "RPM (package)"),
    ("yum", False, "Yum (package)"),
    ("apt", False, "APT (package)"),
    ("aptitude", False, "Aptitude (package)"),
    ("dpkg-deb", False, "Dpkg-deb (package)"),
    ("alien", False, "Alien (package)"),
    ("checkinstall", False, "Checkinstall (package)"),
    ("dh_make", False, "Dh_make (package)"),
    ("debuild", False, "Debuild (package)"),
    ("dpkg-buildpackage", False, "Dpkg-buildpackage (package)"),
    ("dpkg-source", False, "Dpkg-source (package)"),
    ("dpkg-shlibdeps", False, "Dpkg-shlibdeps (package)"),
    ("dpkg-genchanges", False, "Dpkg-genchanges (package)"),
    ("dpkg-gencontrol", False, "Dpkg-gencontrol (package)"),
    ("dpkg-distaddfile", False, "Dpkg-distaddfile (package)"),
    ("dpkg-parsechangelog", False, "Dpkg-parsechangelog (package)"),
    ("dpkg-genchanges", False, "Dpkg-genchanges (package)"),
    ("dpkg-buildflags", False, "Dpkg-buildflags (package)"),
    ("dpkg-architecture", False, "Dpkg-architecture (package)"),
    ("dpkg-shlibdeps", False, "Dpkg-shlibdeps (package)"),
    ("dpkg-gensymbols", False, "Dpkg-gensymbols (package)"),
    ("dpkg-genchanges", False, "Dpkg-genchanges (package)"),
    ("dpkg-distaddfile", False, "Dpkg-distaddfile (package)"),
    ("dpkg-parsechangelog", False, "Dpkg-parsechangelog (package)"),
    ("dpkg-buildpackage", False, "Dpkg-buildpackage (package)"),
    ("dpkg-source", False, "Dpkg-source (package)"),
    ("dpkg-shlibdeps", False, "Dpkg-shlibdeps (package)"),
    ("dpkg-genchanges", False, "Dpkg-genchanges (package)"),
    ("dpkg-gencontrol", False, "Dpkg-gencontrol (package)"),
    ("dpkg-distaddfile", False, "Dpkg-distaddfile (package)"),
    ("dpkg-parsechangelog", False, "Dpkg-parsechangelog (package)"),
    ("dpkg-buildflags", False, "Dpkg-buildflags (package)"),
    ("dpkg-architecture", False, "Dpkg-architecture (package)"),
    ("dpkg-shlibdeps", False, "Dpkg-shlibdeps (package)"),
    ("dpkg-gensymbols", False, "Dpkg-gensymbols (package)"),
    ("dpkg-genchanges", False, "Dpkg-genchanges (package)"),
    ("dpkg-distaddfile", False, "Dpkg-distaddfile (package)"),
    ("dpkg-parsechangelog", False, "Dpkg-parsechangelog (package)"),
    ("dpkg-buildpackage", False, "Dpkg-buildpackage (package)"),
    ("dpkg-source", False, "Dpkg-source (package)"),
    ("dpkg-shlibdeps", False, "Dpkg-shlibdeps (package)"),
    ("dpkg-genchanges", False, "Dpkg-genchanges (package)"),
    ("dpkg-gencontrol", False, "Dpkg-gencontrol (package)"),
    ("dpkg-distaddfile", False, "Dpkg-distaddfile (package)"),
    ("dpkg-parsechangelog", False, "Dpkg-parsechangelog (package)"),
    ("dpkg-buildflags", False, "Dpkg-buildflags (package)"),
    ("dpkg-architecture", False, "Dpkg-architecture (package)"),
    ("dpkg-shlibdeps", False, "Dpkg-shlibdeps (package)"),
    ("dpkg-gensymbols", False, "Dpkg-gensymbols (package)"),
    ("dpkg-genchanges", False, "Dpkg-genchanges (package)"),
    ("dpkg-distaddfile", False, "Dpkg-distaddfile (package)"),
    ("dpkg-parsechangelog", False, "Dpkg-parsechangelog (package)"),
]

@pytest.fixture
def mock_subprocess_run():
    """Mock subprocess.run to prevent actual command execution."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.stdout = "Test output"
        mock_run.return_value.stderr = ""
        yield mock_run

def test_devops_command_validation(mock_subprocess_run):
    """Test DevOps engineer command whitelist validation."""
    for command, expected_success, description in VALID_COMMAND_TEST_CASES:
        success, output = devops_validate_and_execute(command)
        assert success == expected_success, f"Command '{command}' ({description}) should be {'allowed' if expected_success else 'blocked'}"
    
    for command, expected_success, description in INVALID_COMMAND_TEST_CASES:
        success, output = devops_validate_and_execute(command)
        assert success == expected_success, f"Command '{command}' ({description}) should be {'blocked' if expected_success else 'allowed'}"

def test_python_command_validation(mock_subprocess_run):
    """Test Python developer command whitelist validation."""
    for command, expected_success, description in VALID_COMMAND_TEST_CASES:
        success, output = python_validate_and_execute(command)
        assert success == expected_success, f"Command '{command}' ({description}) should be {'allowed' if expected_success else 'blocked'}"
    
    for command, expected_success, description in INVALID_COMMAND_TEST_CASES:
        success, output = python_validate_and_execute(command)
        assert success == expected_success, f"Command '{command}' ({description}) should be {'blocked' if expected_success else 'allowed'}"

def test_react_command_validation(mock_subprocess_run):
    """Test React developer command whitelist validation."""
    for command, expected_success, description in VALID_COMMAND_TEST_CASES:
        success, output = react_validate_and_execute(command)
        assert success == expected_success, f"Command '{command}' ({description}) should be {'allowed' if expected_success else 'blocked'}"
    
    for command, expected_success, description in INVALID_COMMAND_TEST_CASES:
        success, output = react_validate_and_execute(command)
        assert success == expected_success, f"Command '{command}' ({description}) should be {'blocked' if expected_success else 'allowed'}"

def test_command_argument_validation():
    """Test that command arguments are properly validated."""
    # Test valid arguments for specific commands
    valid_args = [
        ("ls -l", True),
        ("ls -a", True),
        ("ls -h", True),
        ("ls -R", True),
        ("ls -F", True),
        ("ls -1", True),
        ("grep -i", True),
        ("grep -v", True),
        ("grep -r", True),
        ("grep -n", True),
        ("grep -c", True),
        ("grep -l", True),
        ("grep -H", True),
        ("grep -h", True),
        ("grep -A 2", True),
        ("grep -B 2", True),
        ("grep -C 2", True),
        ("find . -name '*.py'", True),
        ("find . -type f", True),
        ("find . -mtime 1", True),
        ("find . -size +1M", True),
        ("mkdir -p folder", True),
        ("mkdir -m 755 folder", True),
        ("cp -r dir1 dir2", True),
        ("cp -f file1 file2", True),
        ("cp -p file1 file2", True),
        ("cp -v file1 file2", True),
        ("mv -f file1 file2", True),
        ("mv -i file1 file2", True),
        ("mv -v file1 file2", True),
        ("head -n 10 file.txt", True),
        ("head -c 100 file.txt", True),
        ("tail -n 20 file.txt", True),
        ("tail -c 200 file.txt", True),
        ("tail -f log.txt", True),
        ("sort -n file.txt", True),
        ("sort -r file.txt", True),
        ("sort -u file.txt", True),
        ("uniq -c file.txt", True),
        ("uniq -d file.txt", True),
        ("uniq -u file.txt", True),
        ("wc -l file.txt", True),
        ("wc -w file.txt", True),
        ("wc -c file.txt", True),
        ("date -u", True),
        ("date +%Y-%m-%d", True),
        ("date +%H:%M:%S", True),
        ("ping -c 4 google.com", True),
        ("ping -n 4 google.com", True),
        ("ping -q google.com", True),
        ("ping -i 0.5 google.com", True),
        ("curl -s https://example.com", True),
        ("curl -f https://example.com", True),
        ("curl -o file.html https://example.com", True),
        ("curl -I https://example.com", True),
        ("curl -X POST https://example.com", True),
        ("wget -q https://example.com/file", True),
        ("wget -O file.txt https://example.com", True),
        ("wget -T 10 https://example.com", True),
        ("npm run test", True),
        ("npm test", True),
        ("npm build", True),
        ("npm install", True),
        ("npm install --save-dev", True),
        ("npm install -g package", True),
        ("pytest -x", True),
        ("pytest -v", True),
        ("pytest -k test_name", True),
        ("pytest --cov", True),
        ("pytest --junitxml=results.xml", True),
        ("python -m pytest", True),
        ("python --version", True),
        ("pip install requests", True),
        ("pip freeze", True),
        ("pip list", True),
        ("node --version", True),
        ("node -e 'console.log(\"test\")'", True),
        ("npx --version", True),
        ("npx test", True),
        ("git status", True),
        ("git pull", True),
        ("git push", True),
        ("git clone https://github.com/user/repo.git", True),
        ("git commit -m 'message'", True),
        ("git add .", True),
        ("git branch", True),
    ]
    
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.stdout = "Test output"
        mock_run.return_value.stderr = ""
        for command, expected_success in valid_args:
            success, output = devops_validate_and_execute(command)
            assert success == expected_success, f"Command '{command}' should be {'allowed' if expected_success else 'blocked'}"

def test_command_argument_rejection():
    """Test that invalid command arguments are rejected."""
    invalid_args = [
        ("ls --invalid", False),  # Invalid option
        ("ls --invalid", False),  # Invalid long option
        ("grep -z", False),  # Invalid option
        ("find . -invalid", False),  # Invalid option
        ("mkdir -x", False),  # Invalid option
        ("cp -x", False),  # Invalid option
        ("mv -x", False),  # Invalid option
        ("head -x", False),  # Invalid option
        ("tail -x", False),  # Invalid option
        ("sort -x", False),  # Invalid option
        ("uniq -x", False),  # Invalid option
        ("wc -x", False),  # Invalid option
        ("date +%Y-%m-%d %H:%M:%S", False),  # Invalid format
        ("ping -x", False),  # Invalid option
        ("curl -x", False),  # Invalid option
        ("wget -x", False),  # Invalid option
        ("npm --invalid", False),  # Invalid option
        ("pytest --invalid", False),  # Invalid option
        ("python --invalid", False),  # Invalid option
        ("pip --invalid", False),  # Invalid option
        ("node --invalid", False),  # Invalid option
        ("npx --invalid", False),  # Invalid option
        ("git --invalid", False),  # Invalid option
    ]
    
    for command, expected_success in invalid_args:
        success, output = devops_validate_and_execute(command)
        assert success == expected_success, f"Command '{command}' should be {'blocked' if expected_success else 'allowed'}"

def test_command_positional_argument_validation():
    """Test that positional arguments are properly validated."""
    # Test valid positional arguments (paths within workspace)
    valid_positional = [
        ("ls .", True),
        ("ls src", True),
        ("ls data/", True),
        ("cat file.txt", True),
        ("cat ./file.txt", True),
        ("grep 'pattern' file.txt", True),
        ("find . -name '*.py'", True),
        ("mkdir new_folder", True),
        ("rmdir folder", True),
        ("cp file1.txt file2.txt", True),
        ("mv file1.txt file2.txt", True),
        ("touch new_file.txt", True),
        ("head -n 10 file.txt", True),
        ("tail -f log.txt", True),
        ("sort file.txt", True),
        ("uniq file.txt", True),
        ("wc file.txt", True),
    ]
    
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.stdout = "Test output"
        mock_run.return_value.stderr = ""
        for command, expected_success in valid_positional:
            success, output = devops_validate_and_execute(command)
            assert success == expected_success, f"Command '{command}' should be {'allowed' if expected_success else 'blocked'}"

def test_command_positional_argument_rejection():
    """Test that invalid positional arguments are rejected."""
    invalid_positional = [
        ("ls /etc", False),  # Absolute path outside workspace
        ("cat /etc/passwd", False),  # Absolute path outside workspace
        ("grep 'pattern' /etc/passwd", False),  # Absolute path outside workspace
        ("find / -name '*.py'", False),  # Absolute path outside workspace
        ("mkdir /tmp/folder", False),  # Absolute path outside workspace
        ("cp /etc/passwd /tmp", False),  # Absolute path outside workspace
        ("mv /etc/passwd /tmp", False),  # Absolute path outside workspace
        ("touch /etc/newfile.txt", False),  # Absolute path outside workspace
        ("head -n 10 /etc/passwd", False),  # Absolute path outside workspace
        ("tail -f /var/log/syslog", False),  # Absolute path outside workspace
        ("sort /etc/passwd", False),  # Absolute path outside workspace
        ("uniq /etc/passwd", False),  # Absolute path outside workspace
        ("wc /etc/passwd", False),  # Absolute path outside workspace
        ("ls ../../outside", False),  # Path traversal
        ("cat ../secret.txt", False),  # Path traversal
    ]
    
    for command, expected_success in invalid_positional:
        success, output = devops_validate_and_execute(command)
        assert success == expected_success, f"Command '{command}' should be {'blocked' if expected_success else 'allowed'}"

def test_command_empty_and_none():
    """Test edge cases with empty and None commands."""
    # Empty command
    success, output = devops_validate_and_execute("")
    assert not success, "Empty command should be blocked"
    
    # None command
    success, output = devops_validate_and_execute(None)
    assert not success, "None command should be blocked"

def test_command_timeout_handling():
    """Test that command timeouts are handled gracefully."""
    with patch("subprocess.run") as mock_run:
        # Simulate timeout using the correct subprocess exception
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ls -l", timeout=30)
        
        success, output = devops_validate_and_execute("ls -l")
        assert not success, "Command that times out should return failure"
        assert "timed out" in output.lower(), "Should return timeout message"

def test_command_not_found_handling():
    """Test that command not found errors are handled gracefully."""
    with patch("subprocess.run") as mock_run:
        # Simulate FileNotFoundError on a whitelisted command
        mock_run.side_effect = FileNotFoundError()
        
        # Use a whitelisted command so validation passes and subprocess.run is reached
        success, output = devops_validate_and_execute("ls -l")
        assert not success, "Command that doesn't exist should return failure"
        assert "not found" in output.lower(), "Should return not found message"

def test_command_execution_exception_handling():
    """Test that general execution exceptions are handled gracefully."""
    with patch("subprocess.run") as mock_run:
        # Simulate general exception
        mock_run.side_effect = Exception("Test error")
        
        success, output = devops_validate_and_execute("ls -l")
        assert not success, "Command with execution error should return failure"
        assert "error executing" in output.lower(), "Should return error message"

def test_command_output_truncation():
    """Test that command output is properly truncated."""
    long_output = "a" * 6000  # More than 5000 chars
    
    with patch("subprocess.run") as mock_run:
        # Use str (not bytes) since the real implementation uses text=True
        mock_run.return_value.stdout = long_output
        mock_run.return_value.stderr = ""
        
        success, output = devops_validate_and_execute("ls -l")
        assert success, "Command should succeed"
        assert len(output) <= 5100, "Output should be truncated to ~5000 chars"
        assert output.endswith("... (truncated)"), "Output should indicate truncation"

def test_command_environment_is_copied():
    """Test that environment variables are properly copied (not shared)."""
    # This test ensures we're not passing the same environment reference
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.stdout = "test"
        mock_run.return_value.stderr = ""
        
        # Call the function
        success, output = devops_validate_and_execute("ls -l")
        
        # Check that env was passed and it's a copy
        call_kwargs = mock_run.call_args.kwargs
        assert "env" in call_kwargs, "Environment should be passed"
        env = call_kwargs["env"]
        # The env should be a copy of os.environ, not the same object
        assert env is not os.environ, "Environment should be a copy, not the original"
        assert os.environ.get("PATH") == env.get("PATH"), "Environment should contain same variables"

def test_command_cwd_is_respected():
    """Test that working directory is properly set."""
    test_cwd = "C:\\test\\workspace"
    
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.stdout = "test"
        mock_run.return_value.stderr = ""
        
        success, output = devops_validate_and_execute("ls -l", cwd=test_cwd)
        
        call_kwargs = mock_run.call_args.kwargs
        assert "cwd" in call_kwargs, "Working directory should be passed"
        assert call_kwargs["cwd"] == test_cwd, "Working directory should match provided value"

def test_command_shell_true():
    """Test that shell=True is used for backward compatibility."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.stdout = "test"
        mock_run.return_value.stderr = ""
        
        success, output = devops_validate_and_execute("ls -l")
        
        # shell=True is passed as a keyword argument
        call_args = mock_run.call_args
        assert call_args.args[0] == "ls -l", "Command should be passed as string"
        assert call_args.kwargs["shell"] == True, "shell=True should be used"