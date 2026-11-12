"""Remove the rejected USB3 PD role-error trial from prepared kernels.

Normal builds keep mainline CC-open error recovery. The explicit enable path
is retained for offline regression of the rejected trial and its removal.
"""
from pathlib import Path

FIELD = 'hard_reset_on_data_role_mismatch'
DECL = 'static inline enum tcpm_state hard_reset_state(struct tcpm_port *port);\n\n'
OLD = ('\t\t\ttcpm_log(port,\n'
       '\t\t\t\t "Data role mismatch, initiating error recovery");\n'
       '\t\t\ttcpm_set_state(port, ERROR_RECOVERY, 0);')
NEW = ('\t\t\t/* UMP9620 autonomous CC cannot implement CC_OPEN. Its vendor\n'
       '\t\t\t * policy recovers this protocol error with a bounded PD reset.\n'
       '\t\t\t * Other TCPCs keep the mainline error-recovery path.\n'
       '\t\t\t */\n'
       '\t\t\ttcpm_log(port, "Data role mismatch, initiating %s recovery",\n'
       '\t\t\t\t port->tcpc->hard_reset_on_data_role_mismatch ? "PD hard-reset" : "CC-open");\n'
       '\t\t\ttcpm_set_state(port, port->tcpc->hard_reset_on_data_role_mismatch ?\n'
       '\t\t\t\t       hard_reset_state(port) : ERROR_RECOVERY, 0);')


def apply(tree, enable=False):
    tree = Path(tree)
    header = tree / 'include/linux/usb/tcpm.h'
    source = tree / 'drivers/usb/typec/tcpm/tcpm.c'
    h, s = header.read_text(), source.read_text()
    if not enable:
        # Migrate only our rejected trial in an already prepared local tree.
        # Fresh kernels keep their original descriptor and error handling.
        if FIELD not in h and NEW not in s:
            return
        field = '\tbool hard_reset_on_data_role_mismatch;\n'
        doc = (' * @hard_reset_on_data_role_mismatch:\n'
               ' *\t\tOpt in controllers with autonomous CC to bounded PD hard-reset\n'
               ' *\t\trecovery on a data-role mismatch. Default uses CC-open recovery.\n')
        if h.count(field) != 1 or h.count(doc) != 1 or s.count(NEW) != 1 or s.count(DECL) != 1:
            raise ValueError('cannot remove an unrecognized TCPM role-recovery trial')
        h = h.replace(field, '', 1).replace(doc, '', 1)
        s = s.replace(NEW, OLD, 1).replace(DECL, '', 1)
        header.write_text(h)
        source.write_text(s)
        return
    if FIELD not in h:
        anchor = 'struct tcpc_dev {\n\tstruct fwnode_handle *fwnode;\n'
        if h.count(anchor) != 1:
            raise ValueError('TCPM descriptor layout changed')
        h = h.replace(anchor, anchor + '\tbool hard_reset_on_data_role_mismatch;\n', 1)
        doc = ' * @set_roles:'
        if h.count(doc) != 1:
            raise ValueError('TCPM descriptor documentation changed')
        h = h.replace(doc,
            ' * @hard_reset_on_data_role_mismatch:\n'
            ' *\t\tOpt in controllers with autonomous CC to bounded PD hard-reset\n'
            ' *\t\trecovery on a data-role mismatch. Default uses CC-open recovery.\n' + doc, 1)
    elif h.count('\tbool hard_reset_on_data_role_mismatch;') != 1:
        raise ValueError('unexpected existing TCPM role-recovery descriptor')
    if NEW not in s:
        if s.count(OLD) != 1:
            raise ValueError('TCPM role-error branch changed')
        s = s.replace(OLD, NEW, 1)
    anchor = 'static void tcpm_pd_rx_handler(struct kthread_work *work)'
    if DECL not in s:
        if s.count(anchor) != 1:
            raise ValueError('TCPM receive handler changed')
        s = s.replace(anchor, DECL + anchor, 1)
    header.write_text(h)
    source.write_text(s)
