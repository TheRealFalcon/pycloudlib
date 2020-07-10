# This file is part of pycloudlib. See LICENSE file for license information.
"""Base class for all other clouds to provide consistent set of functions."""

import datetime
import getpass
import logging
from abc import ABC, abstractmethod

from pycloudlib.key import KeyPair
from pycloudlib.streams import Streams


class BaseCloud(ABC):
    """Base Cloud Class."""

    _type = 'base'

    def __init__(self, tag):
        """Initialize base cloud class.

        Args:
            tag: string used to name and tag resources with
        """
        self._log = logging.getLogger(__name__)

        _username = getpass.getuser()
        self.key_pair = KeyPair(
            '/home/%s/.ssh/id_rsa.pub' % _username, name=_username
        )
        self.tag = '%s-%s' % (
            tag, datetime.datetime.now().strftime("%m%d-%H%M%S")
        )

    @abstractmethod
    def delete_image(self, image_id):
        """Delete an image.

        Args:
            image_id: string, id of the image to delete
        """
        raise NotImplementedError

    @abstractmethod
    def released_image(self, release, **kwargs):
        """ID of the latest released image for a particular release.

        Args:
            release: The release to look for

        Returns:
            A single string with the latest released image ID for the
            specified release.

        """
        raise NotImplementedError

    @abstractmethod
    def daily_image(self, release, **kwargs):
        """ID of the latest daily image for a particular release.

        Args:
            release: The release to look for

        Returns:
            A single string with the latest daily image ID for the
            specified release.

        """
        raise NotImplementedError

    @abstractmethod
    def image_serial(self, image_id):
        """Find the image serial of the latest daily image for a particular release.

        Args:
            image_id: string, Ubuntu image id

        Returns:
            string, serial of latest image

        """
        raise NotImplementedError

    @abstractmethod
    def get_instance(self, instance_id):  # () -> BaseInstance
        """Get an instance by id.

        Args:
            instance_id:

        Returns:
            An instance object to use to manipulate the instance further.

        """
        raise NotImplementedError

    @abstractmethod
    def launch(self, image_id, instance_type=None, user_data=None,
               wait=False, **kwargs):  # () -> BaseInstance
        """Launch an instance.

        Args:
            image_id: string, image ID to use for the instance
            instance_type: string, type of instance to create
            user_data: used by cloud-init to run custom scripts/configuration
            wait: wait for instance to be live
            **kwargs: dictionary of other arguments to pass to launch

        Returns:
            An instance object to use to manipulate the instance further.

        """
        raise NotImplementedError

    @abstractmethod
    def snapshot(self, instance, clean=True, wait=True, **kwargs):
        """Snapshot an instance and generate an image from it.

        Args:
            instance: Instance to snapshot
            clean: run instance clean method before taking snapshot
            wait: wait for instance to get created

        Returns:
            An image id

        """
        raise NotImplementedError

    def list_keys(self):
        """List ssh key names present on the cloud for accessing instances.

        Returns:
           A list of strings of key pair names accessible to the cloud.

        """
        raise NotImplementedError

    def use_key(self, public_key_path, private_key_path=None, name=None):
        """Use an existing key.

        Args:
            public_key_path: path to the public key to upload
            private_key_path: path to the private key
            name: name to reference key by
        """
        self._log.debug('using SSH key from %s', public_key_path)
        self.key_pair = KeyPair(public_key_path, private_key_path, name)

    @staticmethod
    def _streams_query(filters, daily=True):
        """Query the cloud-images streams applying a filter.

        Args:
            filters: list of 'field=value' strings, filters to apply
            daily: bool, query the 'daily' stream (default: True)

        Returns:
            a list of dictionaries containing the streams metadata of the
            images matching 'filters'.

        """
        if daily:
            mirror_url = 'https://cloud-images.ubuntu.com/daily'
        else:
            mirror_url = 'https://cloud-images.ubuntu.com/releases'

        stream = Streams(
            mirror_url=mirror_url,
            keyring_path='/usr/share/keyrings/ubuntu-cloudimage-keyring.gpg'
        )

        return stream.query(filters)
