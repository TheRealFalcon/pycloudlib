# This file is part of pycloudlib. See LICENSE file for license information.
"""Base class for all instances to provide consistent set of functions."""

import logging
import time

import paramiko
from paramiko.ssh_exception import (
    AuthenticationException,
    BadHostKeyException,
    PasswordRequiredException,
    SSHException
)

from pycloudlib.result import Result
from pycloudlib.util import shell_quote, shell_pack, subp


class BaseInstance:
    """Base instance object."""

    _type = 'base'

    def __init__(self, key_pair):
        """Set up instance."""
        self._log = logging.getLogger(__name__)
        self._ssh_client = None
        self._sftp_client = None
        self._tmp_count = 0

        self.name = ''
        self.boot_timeout = 120
        self.key_pair = key_pair
        self.port = '22'
        self.username = 'ubuntu'

    @property
    def ip(self):  # pylint: disable=C0103
        """Return IP address of instance.

        Returns:
            IP address assigned to instance.

        """
        return ''

    def __del__(self):
        """Cleanup of instance."""
        if self._sftp_client:
            try:
                self._sftp_client.close()
            except SSHException:
                self._log.warning('Failed to close SFTP connection.')
            self._sftp_client = None
        if self._ssh_client:
            try:
                self._ssh_client.close()
            except SSHException:
                self._log.warning('Failed to close SSH connection.')
            self._ssh_client = None

    def clean(self):
        """Clean an instance to make it look prestine.

        This will clean out specifically the cloud-init files and system logs.
        """
        self.execute('sudo cloud-init clean --logs')
        self.execute('sudo rm -rf /var/log/syslog')

    def console_log(self):
        """Return the instance console log."""
        raise NotImplementedError

    def delete(self, wait=True):
        """Delete the instance.

        Args:
            wait: wait for instance to be deleted
        """
        raise NotImplementedError

    def execute(self, command, stdin=None, description=None):
        """Execute command in instance, recording output, error and exit code.

        Assumes functional networking and execution with the target filesystem
        being available at /.

        Args:
            command: the command to execute as root inside the image. If
                     command is a string, then it will be executed as:
                     `['sh', '-c', command]`
            stdin: bytes content for standard in
            description: purpose of command

        Returns:
            Result object

        """
        if isinstance(command, str):
            command = ['sh', '-c', command]

        if description:
            self._log.debug(description)
        else:
            self._log.debug('executing: %s', shell_quote(command))

        if self._type == 'lxd':
            base_cmd = ['lxc', 'exec', self.name, '--']
            return subp(base_cmd + list(command))

        # multipass handling of redirects is buggy, so we don't bind
        # stdin to /dev/null for the moment (shortcircuit_stdin=False).
        # See: https://github.com/CanonicalLtd/multipass/issues/667
        if self._type == 'kvm':
            base_cmd = ['multipass', 'exec', self.name, '--']
            return subp(base_cmd + list(command), shortcircuit_stdin=False)

        return self._ssh(list(command), stdin=stdin)

    def install(self, packages):
        """Install specific packages.

        Args:
            packages: string or list of package(s) to install

        Returns:
            result from install

        """
        if isinstance(packages, str):
            packages = packages.split(' ')

        self.execute(['sudo', 'apt-get', 'update'])
        return self.execute(
            [
                'DEBIAN_FRONTEND=noninteractive',
                'sudo', 'apt-get', 'install', '--yes'
            ] + packages
        )

    def pull_file(self, remote_path, local_path):
        """Copy file at 'remote_path', from instance to 'local_path'.

        Args:
            remote_path: path on remote instance
            local_path: local path
        """
        self._log.debug('pulling file %s to %s', remote_path, local_path)

        sftp = self._sftp_connect()
        sftp.get(remote_path, local_path)

    def push_file(self, local_path, remote_path):
        """Copy file at 'local_path' to instance at 'remote_path'.

        Args:
            local_path: local path
            remote_path: path on remote instance
        """
        self._log.debug('pushing file %s to %s', local_path, remote_path)

        sftp = self._sftp_connect()
        sftp.put(local_path, remote_path)

    def restart(self, wait=True):
        """Restart an instance."""
        raise NotImplementedError

    def run_script(self, script, description=None):
        """Run script in target and return stdout.

        Args:
            script: script contents
            description: purpose of script

        Returns:
            result from script execution

        """
        # Just write to a file, add execute, run it, then remove it.
        shblob = '; '.join((
            'set -e',
            's="$1"',
            'shift',
            'cat > "$s"',
            'trap "rm -f $s" EXIT',
            'chmod +x "$s"',
            '"$s" "$@"'))
        return self.execute(
            ['sh', '-c', shblob, 'runscript', self._tmpfile()],
            stdin=script, description=description)

    def shutdown(self, wait=True):
        """Shutdown the instance.

        Args:
            wait: wait for the instance to shutdown
        """
        raise NotImplementedError

    def start(self, wait=True):
        """Start the instance.

        Args:
            wait: wait for the instance to start.
        """
        raise NotImplementedError

    def update(self):
        """Run apt-get update/upgrade on instance.

        Returns:
            result from upgrade

        """
        self.execute(['sudo', 'apt-get', 'update'])
        return self.execute([
            'DEBIAN_FRONTEND=noninteractive',
            'sudo', 'apt-get', '--yes', 'upgrade'
        ])

    def wait(self):
        """Wait for instance to be up and cloud-init to be complete."""
        raise NotImplementedError

    def wait_for_delete(self):
        """Wait for instance to be deleted."""
        raise NotImplementedError

    def wait_for_stop(self):
        """Wait for instance stop."""
        raise NotImplementedError

    def _ssh(self, command, stdin=None):
        """Run a command via SSH.

        Args:
            command: string or list of the command to run
            stdin: optional, values to be passed in

        Returns:
            tuple of stdout, stderr and the return code

        """
        client = self._ssh_connect()

        cmd = shell_pack(command)
        fp_in, fp_out, fp_err = client.exec_command(cmd)
        channel = fp_in.channel

        if stdin is not None:
            fp_in.write(stdin)
            fp_in.close()

        channel.shutdown_write()

        out = fp_out.read()
        err = fp_err.read()
        return_code = channel.recv_exit_status()

        out = '' if not out else out.rstrip().decode("utf-8")
        err = '' if not err else err.rstrip().decode("utf-8")

        return Result(out, err, return_code)

    def _ssh_connect(self):
        """Connect to instance via SSH."""
        if self._ssh_client and self._ssh_client.get_transport().is_active():
            return self._ssh_client

        logging.getLogger("paramiko").setLevel(logging.INFO)
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            private_key = paramiko.RSAKey.from_private_key_file(
                self.key_pair.private_key_path
            )
        except PasswordRequiredException:
            self._log.error('RSA Key requires password!')
            raise

        retries = 60
        while retries:
            last_exception = None
            try:
                client.connect(username=self.username, hostname=self.ip,
                               port=self.port, pkey=private_key)
                self._ssh_client = client
                return client
            except (ConnectionRefusedError, AuthenticationException,
                    BadHostKeyException, ConnectionResetError, SSHException,
                    OSError) as e:
                last_exception = e
                retries -= 1
                time.sleep(10)

        self._log.error('Failed ssh connection to %s@%s:%s after 10 minutes',
                        self.username, self.ip, self.port)
        raise last_exception

    def _sftp_connect(self):
        """Connect to instance via SFTP."""
        if (self._sftp_client and
                self._sftp_client.get_channel().get_transport().is_active()):
            return self._sftp_client

        logging.getLogger("paramiko").setLevel(logging.INFO)

        # _ssh_connect() implements the required retry logic.
        client = self._ssh_connect()
        sftpclient = client.open_sftp()
        self._sftp_client = sftpclient
        return sftpclient

    def _tmpfile(self):
        """Get a tmp file in the target.

        Returns:
            path to new file in target

        """
        path = "/tmp/%s-%04d" % (type(self).__name__, self._tmp_count)
        self._tmp_count += 1
        return path

    def _wait_for_system(self):
        """Wait until system is fully booted and cloud-init has finished."""
        result = self.execute(
            ['cloud-init', 'status', '--wait'],
            description='waiting for start'
        )

        if result.failed:
            raise OSError('cloud-init failed to start: %s' % result.stdout)
