import os
import socket
from shutil import rmtree

import jinja2
import time
from oslo_log import log
from oslo_utils import fileutils

from ironic_lib import utils as ironic_utils
from ironic.common import exception, pxe_utils, boot_devices, states
from ironic.common import utils
from ironic.common.i18n import _, _LE, _LI, _LW
from ironic.common.pxe_utils import get_root_dir
from ironic.conductor import task_manager
from ironic.conductor import utils as manager_utils
from ironic.conf import CONF
from ironic.drivers import base
from ironic.drivers.modules import deploy_utils


LOG = log.getLogger(__name__)

REQUIRED_PROPERTIES = ['user_kernel',
                       'user_ramdisk',
                       'management_ip',
                       'management_netmask',
                       'management_gateway']
PXE_CFG_DIR_NAME = 'pxelinux.cfg'
HOSTNAME_PREFIX = 'Host-'
AUTO_FILE_DIR = "/var/www/html/auto/"

class PXEAutoDeploy(base.DeployInterface):

    def __init__(self):
        pass

    def clean_up(self, task):

        extra_info = task.node.extra
        pxe_boot_interface_mac = extra_info.get('boot_detailed').get('pxe_interface')
        pxe_boot_interface_mac.replace('-', ':')
        for port in task.ports:
            if port.address == pxe_boot_interface_mac:
                client_id = port.extra.get('client-id')
                ironic_utils.unlink_without_raise(self._get_pxe_mac_path(port.address, client_id=client_id))

        pxe_config_file_path = pxe_utils.get_pxe_config_file_path(task.node.uuid)
        fileutils.delete_if_exists(pxe_config_file_path)
        if os.path.exists(os.path.join(CONF.pxe.tftp_root, task.node.uuid)):
            rmtree(os.path.join(CONF.pxe.tftp_root, task.node.uuid))

        auto_file_name = task.node.uuid + '_auto.cfg'
        fileutils.delete_if_exists(AUTO_FILE_DIR + auto_file_name)

    @task_manager.require_exclusive_lock
    def deploy(self, task):

        manager_utils.node_power_action(task, states.REBOOT)

        return states.DEPLOYWAIT

    def get_properties(self):
        pass

    @task_manager.require_exclusive_lock
    def prepare(self, task):

        # No need to update dhcp with standalone mode

        self._create_auto_config(task)
        self._create_pxe_config(task)

        deploy_utils.try_set_boot_device(task, boot_devices.PXE)

    def _create_auto_config(self, task):
        auto_info = {}
        managemenet_ip = task.node.instance_info.get('management_ip')
        auto_info['management_ip'] = managemenet_ip
        auto_info['management_netmask'] = \
            task.node.instance_info.get('management_netmask')
        auto_info['management_gateway'] = \
            task.node.instance_info.get('management_gateway')
        auto_info['hostname'] = \
            HOSTNAME_PREFIX + managemenet_ip.replace('.', '-')
        auto_info['os_ver'] = \
            task.node.instance_info.get('os_ver')
        auto_info['server_ip'] = CONF.my_ip
        extra_info = task.node.extra
        pxe_boot_interface_mac = self._get_boot_interface_mac(task)
        for nic in extra_info.get('nic_detailed'):
            address = nic.get('mac_address')
            LOG.info('address: %s', address)
            if nic.get('mac_address') == pxe_boot_interface_mac:
                auto_info['management_port'] = nic.get('name')
                break

        fileutils.ensure_tree(AUTO_FILE_DIR)
        auto_file_name = task.node.uuid + '_auto.cfg'
        auto_file_path = AUTO_FILE_DIR + auto_file_name
        tmpl_path, tmpl_file = os.path.split(CONF.pxe_auto.pxe_auto_template)
        env = jinja2.Environment(loader=jinja2.FileSystemLoader(tmpl_path))
        template = env.get_template(tmpl_file)
        auto_info = template.render({'auto_info': auto_info,
                                     'server_ip': CONF.my_ip,
                                     'repo_server_ip': CONF.pxe_auto.repo_server,
                                     'UUID': task.node.uuid,
                                      })
        utils.write_to_file(auto_file_path, auto_info)

    def _get_boot_interface_mac(self, task):
        extra_info = task.node.extra
        # pxe_interface like '01-6c-92-bf-0c-9c-d9'. '01-' is not needed.
        pxe_interface = extra_info.get('boot_detailed').get('pxe_interface')[3:]
        return pxe_interface.replace('-', ':')

    def _create_pxe_config(self, task):
        pxe_options = self._build_pxe_options(task.node)

        pxe_config_template = CONF.pxe.pxe_config_template
        node_uuid = task.node.uuid
        root_dir = CONF.pxe.tftp_root
        fileutils.ensure_tree(os.path.join(root_dir, node_uuid))
        fileutils.ensure_tree(os.path.join(root_dir, PXE_CFG_DIR_NAME))

        pxe_config_file_path = pxe_utils.get_pxe_config_file_path(node_uuid)
        tmpl_path, tmpl_file = os.path.split(pxe_config_template)
        env = jinja2.Environment(loader=jinja2.FileSystemLoader(tmpl_path))
        template = env.get_template(tmpl_file)
        pxe_config = template.render({'pxe_options': pxe_options,
                                      'server_ip': CONF.my_ip,
                                      'UUID': node_uuid,
                                })
        utils.write_to_file(pxe_config_file_path, pxe_config)
        self._link_mac_pxe_configs(task)

    def _get_pxe_mac_path(self, mac, delimiter='-', client_id=None):
        """Convert a MAC address into a PXE config file name.

        :param mac: A MAC address string in the format xx:xx:xx:xx:xx:xx.
        :param delimiter: The MAC address delimiter. Defaults to dash ('-').
        :param client_id: client_id indicate InfiniBand port.
                          Defaults is None (Ethernet)
        :returns: the path to the config file.

        """
        mac_file_name = mac.replace(':', delimiter).lower()
        if not CONF.pxe.ipxe_enabled:
            hw_type = '01-'
            if client_id:
                hw_type = '20-'
            mac_file_name = hw_type + mac_file_name

        return os.path.join(get_root_dir(), PXE_CFG_DIR_NAME, mac_file_name)

    def _link_mac_pxe_configs(self, task):
        def create_link(mac_path):
            ironic_utils.unlink_without_raise(mac_path)
            relative_source_path = os.path.relpath(
                pxe_config_file_path, os.path.dirname(mac_path))
            utils.create_link_without_raise(relative_source_path, mac_path)

        pxe_config_file_path = pxe_utils.get_pxe_config_file_path(task.node.uuid)
        pxe_boot_interface_mac = self._get_boot_interface_mac(task)
        LOG.info("pxe_boot_interface_mac: %s", pxe_boot_interface_mac)
        for port in task.ports:
            LOG.info("port.address: %s", port.address)
            if port.address == pxe_boot_interface_mac:
                client_id = port.extra.get('client-id')
                create_link(self._get_pxe_mac_path(port.address, client_id=client_id))

    def _build_pxe_options(self, node):
        pxe_info = {}
        root_dir = pxe_utils.get_root_dir()
        for label in ('user_kernel', 'user_ramdisk'):
            pxe_info[label] = \
                os.path.join(root_dir, node.instance_info.get(label))
        return pxe_info

    def take_over(self, task):
        pass

    def tear_down(self, task):
        manager_utils.node_power_action(task, states.POWER_OFF)

    def validate(self, task):
        info = task.node.instance_info

        for item in REQUIRED_PROPERTIES:
            if not info.get(item):
                error_msg = _("Cannot validate driver deploy. Some parameters were missing"
                              " in node's instance_info")
                exc_msg = _("%(error_msg)s. Missing are: %(missing_info)s")
                raise exception.MissingParameterValue(
                    exc_msg % {'error_msg': error_msg, 'missing_info': item})

    def pxeauto(self, task, data):
        task.upgrade_lock()

        node = task.node
        LOG.info('Pxeauto info for node %(node)s with '
                  'progress info %(data)s',
                  {'node': node.uuid, 'data': data})

        # Parse progress info
        title = data['Title']
        progress = float(data['InstallProgress']) * 100
        LOG.info('data[\'InstallProgress\']: %s', data['InstallProgress'])
        LOG.info('progress: %f', progress)

        if progress == 60:
            task.process_event('resume')
            LOG.info('resume...')

        if progress == 100:
            deploy_utils.try_set_boot_device(task, boot_devices.DISK)
            manager_utils.node_power_action(task, states.REBOOT)
            ret = self.check_conn(node.instance_info.get('management_ip'), 22)
            if ret == 'success':
                task.process_event('done')
                LOG.info(_LI('Deployment to node %s done'), task.node.uuid)


    def check_conn(self, address, port):
        sock = socket.socket()
        frequency = 0
        while True:
            try:
                sock.connect((address, port))
                LOG.info("Connected to %s on port %s", address, port)
                return "success"
            except socket.error, e:
                LOG.info("Connection to %s on port %s failed: %s,"
                         " already wait: %s s", address, port, e, frequency*3)
                frequency += 1
                time.sleep(3)
