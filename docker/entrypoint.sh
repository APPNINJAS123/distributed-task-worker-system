service openvswitch-switch start > /dev/null 2>&1
pgrep ovs-vswitchd > /dev/null || ovs-vswitchd --pidfile --detach --log-file > /dev/null 2>&1
exec "$@"
