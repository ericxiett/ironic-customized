from ironic.drivers import base
from ironic.drivers.modules import ipmitool, agent, inspector, pxe_auto_deploy


class PXEAutoAndIPMIToolDriver(base.BaseDriver):

    def __init__(self):
        self.power = ipmitool.IPMIPower()
        self.deploy = pxe_auto_deploy.PXEAutoDeploy()
        self.console = ipmitool.IPMIShellinaboxConsole()
        self.raid = agent.AgentRAID()
        self.inspect = inspector.Inspector.create_if_enabled(
            'pxeauto_ipmitool')
        self.management = ipmitool.IPMIManagement()