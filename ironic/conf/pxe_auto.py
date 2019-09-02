import os

from oslo_config import cfg
from oslo_utils import netutils

from ironic.common.i18n import _

opts = [
    cfg.StrOpt('pxe_auto_template',
               default=os.path.join(
                   '$pybasedir', 'drivers/modules/auto.template'),
               help=_('On ironic-conductor node, template file for PXE '
                      'auto file.')),

    cfg.StrOpt('repo_server',
               default=netutils.get_my_ipv4(),
               help=_('The IP of the repository server which provides '
                      'softwares of distribute OS.')),
]

def register_opts(conf):
    conf.register_opts(opts, group='pxe_auto')