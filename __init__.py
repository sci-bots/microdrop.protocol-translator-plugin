import os
from collections import Counter
import copy
import cStringIO as StringIO
import json

from microdrop.app_context import get_app
from microdrop.plugin_helpers import get_plugin_info
from microdrop.plugin_manager import (PluginGlobals, Plugin, IPlugin, implements)
import path_helpers as ph
from pygtkhelpers.gthreads import gtk_threadsafe
import gtk
import microdrop as md
import pydash
import zmq_plugin as zp
from logging_helpers import _L
from microdrop_utility.gui import yesno

from ._version import get_versions
__version__ = get_versions()['version']
del get_versions

PluginGlobals.push_env('microdrop.managed')


def upgrade_protocol(protocol):
    def convert_step(step_i):
        step_i = step_i.copy()
        step = {}
        if 'dmf_control_board_plugin' in step_i:
            pydash.merge(step, {"microdrop.electrode_controller_plugin":
                {"Voltage (V)": step_i['dmf_control_board_plugin']['voltage'],
                 "Duration (s)": step_i['dmf_control_board_plugin']['duration'] * 1e-3,
                 "Frequency (Hz)": step_i['dmf_control_board_plugin']['frequency']},
                "wheelerlab.dmf_control_board_plugin":
                {"volume_threshold": step_i['dmf_control_board_plugin']['feedback_options']['action']['percent_threshold'] / 100.}})
            del step_i['dmf_control_board_plugin']
        if 'droplet_planning_plugin' in step:
            del step['droplet_planning_plugin']['transition_duration_ms']
        return pydash.merge(step_i, step)

    protocol_dict = protocol.to_dict()
    steps = map(convert_step, protocol_dict['steps'])
    protocol_dict['steps'] = steps
    return protocol_dict


def protocol_dict_to_json(protocol_dict, ostream=None, validate=True,
                          json_kwargs=None):
    if ostream is None:
        ostream = StringIO.StringIO()
        return_required = True
    else:
        return_required = False

    if validate:
        md.protocol.VALIDATORS['protocol'].validate(protocol_dict)

    def serialize_func(obj):
        return json.dump(obj=obj, fp=ostream,
                         cls=zp.schema.PandasJsonEncoder,
                         **(json_kwargs or {}))

    md.protocol.serialize_protocol(protocol_dict, serialize_func)

    if return_required:
        return ostream.getvalue()


class ProtocolTranslatorPlugin(Plugin):
    """
    This class is automatically registered with the PluginManager.
    """
    implements(IPlugin)
    version = __version__
    plugin_name = get_plugin_info(ph.path(__file__).parent).plugin_name

    def __init__(self):
        self.menu = None

    def on_plugin_enable(self):
        @gtk_threadsafe
        def init_ui():
            if self.menu is None:
                # Schedule initialization of menu user interface.  Calling
                # `create_ui()` directly is not thread-safe, since it includes GTK
                # code.
                self.create_ui()
            else:
                self.menu.show()

        init_ui()

    def on_plugin_disable(self):
        if self.menu is not None:
            self.menu.hide()

    def create_ui(self):
        self.menu = gtk.MenuItem('E_xport 2.35+ protocol...')
        self.menu.set_tooltip_text('Export protocol compatible with MicroDrop '
                                   '2.35+.')
        self.menu.show_all()
        app = get_app()
        # Add main DropBot menu to MicroDrop `Tools` menu.
        app.main_window_controller.menu_tools.append(self.menu)
        self.menu.connect('activate', lambda menu_item:
                          self._export_protocol())

    @gtk_threadsafe
    def _export_protocol(self):
        app = get_app()

        filter_ = gtk.FileFilter()
        filter_.set_name(' MicroDrop protocols (*.json)')
        filter_.add_pattern("*.json")

        dialog = gtk.FileChooserDialog(title="Export protocol",
                                       action=gtk.FILE_CHOOSER_ACTION_SAVE,
                                       buttons=(gtk.STOCK_CANCEL,
                                                gtk.RESPONSE_CANCEL,
                                                gtk.STOCK_SAVE,
                                                gtk.RESPONSE_OK))
        dialog.add_filter(filter_)
        dialog.set_default_response(gtk.RESPONSE_OK)
        dialog.set_current_name(app.protocol.name)
        dialog.set_current_folder(os.path.join(app.get_device_directory(),
                                               app.dmf_device.name,
                                               "protocols"))
        response = dialog.run()
        try:
            if response == gtk.RESPONSE_OK:
                filename = ph.path(dialog.get_filename())
                if filename.ext.lower() != '.json':
                    filename = filename + '.json'
                logger = _L()  # use logger with method context
                try:
                    with open(filename, 'w') as output:
                        protocol_dict_to_json(upgrade_protocol(app.protocol),
                                              ostream=output, validate=False,
                                              json_kwargs={'indent': 2})
                except md.protocol.SerializationError, exception:
                    plugin_exception_counts = Counter([e['plugin'] for e in
                                                       exception.exceptions])
                    logger.info('%s: `%s`', exception, exception.exceptions)
                    result = yesno('Error exporting data for the following '
                                   'plugins: `%s`\n\n'
                                   'Would you like to exclude this data and '
                                   'export anyway?' %
                                   ', '.join(sorted(plugin_exception_counts
                                                    .keys())))
                    if result == gtk.RESPONSE_YES:
                        # Delete plugin data that is causing serialization
                        # errors.
                        protocol = copy.deepcopy(app.protocol)
                        protocol.remove_exceptions(exception.exceptions,
                                                   inplace=True)
                        with open(filename, 'w') as output:
                            protocol_dict_to_json(upgrade_protocol(app.protocol),
                                                  ostream=output,
                                                  validate=False,
                                                  json_kwargs={'indent': 2})
                    else:
                        # Abort export.
                        logger.warn('Export cancelled.')
                        return
                logger.info('exported protocol to %s', filename)
                app = get_app()
                parent_window = app.main_window_controller.view
                message = 'Exported protocol to:\n\n%s' % filename
                ok_dialog = gtk.MessageDialog(parent=parent_window,
                                              message_format=message,
                                              type=gtk.MESSAGE_OTHER,
                                              buttons=gtk.BUTTONS_OK)
                # Increase default dialog size.
                ok_dialog.set_size_request(450, 150)
                ok_dialog.props.title = 'Export complete'
                ok_dialog.props.use_markup = True
                ok_dialog.run()
                ok_dialog.destroy()
        finally:
            dialog.destroy()


PluginGlobals.pop_env()
