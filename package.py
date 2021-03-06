import sublime, sublime_plugin
import os
import time
import json

from . import util
from . import context
from . import processor

from .salesforce.lib.panel import Printer


class CombinePackageXml(sublime_plugin.WindowCommand):
    def __init__(self, *args, **kwargs):
        super(CombinePackageXml, self).__init__(*args, **kwargs)

    def run(self, dirs):
        self.settings = context.get_settings()

        all_types = {}
        for _dir in dirs:
            for dirpath, dirnames, filenames in os.walk(_dir):
                for filename in filenames:
                    if filename.endswith("-meta.xml"): continue
                    if not filename.endswith(".xml"): continue

                    # Package file name
                    package_xml = os.path.join(dirpath, filename)

                    # Read package.xml content
                    with open(package_xml, "rb") as fp:
                        content = fp.read()

                    """ Combine types sample: [
                        {"ApexClass": ["test"]},
                        {"ApexTrigger": ["test"]}
                    ]
                    """
                    try:
                        _types = util.build_package_types(content)
                    except xml.parsers.expat.ExpatError as ee:
                        message = "%s parse error: %s" % (package_xml, str(ee))
                        Printer.get("error").write(message)
                        if not sublime.ok_cancel_dialog(message, "Skip?"): return
                        continue
                    except KeyError as ex:
                        if self.settings["debug_mode"]:
                            print ("%s is not valid package.xml" % package_xml)
                        continue

                    for _type in _types:
                        members = _types[_type]

                        if _type in all_types:
                            members.extend(all_types[_type])
                            members = list(set(members))
                        
                        all_types[_type] = sorted(members)

        if not all_types:
            Printer.get("error").write_start().write("No available package.xml to combine")
            return

        # print (json.dumps(all_types, indent=4))
        metadata_objects = []
        for _type in all_types:
            metadata_objects.append(
                "<types>%s<name>%s</name></types>" % (
                    "".join(["<members>%s</members>" % m for m in all_types[_type]]),
                    _type
                )
            )

        self.package_xml_content = """<?xml version="1.0" encoding="UTF-8"?>
            <Package xmlns="http://soap.sforce.com/2006/04/metadata">
                {metadata_objects}
                <version>{api_version}.0</version>
            </Package>
        """.format(
            metadata_objects="".join(metadata_objects),
            api_version=self.settings["api_version"]
        )

        package_path = os.path.join(dirs[0], "combined package.xml")
        sublime.active_window().show_input_panel("Input Package.xml Path", 
            package_path, self.on_input_package_path, None, None)

    def on_input_package_path(self, package_path):
        # Check input
        if not package_path:
            message = 'Invalid path, do you want to try again?'
            if not sublime.ok_cancel_dialog(message, "Try Again?"): return
            self.window.show_input_panel("Please Input Name: ", "", 
                self.on_input_extractto, None, None)
            return

        base = os.path.split(package_path)[0]
        if not os.path.exists(base):
            os.makedirs(base)

        with open(package_path, "wb") as fp:
            fp.write(util.format_xml(self.package_xml_content))

        view = sublime.active_window().open_file(package_path)
    
    def is_visible(self, dirs):
        if not dirs: return False
        return True

class ReloadProjectCache(sublime_plugin.WindowCommand):
    def __init__(self, *args, **kwargs):
        super(ReloadProjectCache, self).__init__(*args, **kwargs)

    def run(self, callback_command=None):
        self.settings = context.get_settings()
        _types = {}
        for m in self.settings["all_metadata_objects"]:
            # Add parent metadata object
            _types[m] = ["*"]

            # Add child metadata object
            if "childXmlNames" in self.settings[m]:
                child_xml_names = self.settings[m]["childXmlNames"]
                if isinstance(child_xml_names, str):
                    child_xml_names = [child_xml_names]

                for c in child_xml_names:
                    _types[c] = ["*"]

        processor.handle_reload_project_cache(_types, callback_command)

    def is_enabled(self):
        self.settings = context.get_settings()
        cache = os.path.join(self.settings["workspace"], ".config", "metadata.json")
        return os.path.isfile(cache)

class BuildPackageXml(sublime_plugin.WindowCommand):
    def __init__(self, *args, **kwargs):
        super(BuildPackageXml, self).__init__(*args, **kwargs)

    def run(self):
        package_cache = os.path.join(self.settings["workspace"], ".config", "package.json")
        if not os.path.exists(package_cache):
            return self.window.run_command("reload_project_cache", {
                "callback_command": "build_package_xml"
            })

        self.package = json.loads(open(package_cache).read())

        view = util.get_view_by_name("package.xml")
        types = view.settings().get("types") if view else {}
        if not types: types = {}

        self.members = []
        for metadata_object in sorted(self.package.keys()):
            if not self.package[metadata_object]: continue
            if metadata_object in types:
                display = "[√]" + metadata_object
            else:
                display = "[x]" + metadata_object
            self.members.append(display)

            for mem in self.package[metadata_object]:
                if metadata_object in types and mem in types[metadata_object]:
                    mem = "[√]" + mem
                else:
                    mem = "[x]" + mem
                self.members.append("    %s" % mem)

        # Get the last subscribe index
        selected_index = view.settings().get("selected_index") if view else 0
        if not selected_index: selected_index = 0

        self.window.show_quick_panel(self.members, self.on_done, 
            sublime.MONOSPACE_FONT, selected_index)

    def get_metadat_object(self, index):
        metadata_object = None
        while self.members[index].startswith("    "):
            index -= 1
            metadata_object = self.members[index]
            if not metadata_object.startswith("    "):
                break

        return metadata_object

    def on_done(self, index):
        if index == -1: return
        chosen_element = self.members[index]

        if "    " in chosen_element:
            base, chosen_member = chosen_element.split("    ")
            is_chosen = chosen_member[:3] == "[√]"
            chosen_member = chosen_member[3:]
            chosen_metadata_object = self.get_metadat_object(index)[3:]
        else:
            chosen_metadata_object, chosen_member = chosen_element, None
            is_chosen = chosen_metadata_object[:3] == "[√]"
            chosen_metadata_object = chosen_metadata_object[3:]

        view = util.get_view_by_name("package.xml")
        if not view: 
            view = self.window.new_file()
            view.set_syntax_file("Packages/XML/xml.tmLanguage")
            view.run_command("new_view", {
                "name": "package.xml",
                "input": ""
            })
        view.settings().set("selected_index", index)
        self.window.focus_view(view)

        types = view.settings().get("types")
        if not types: types = {}

        if not chosen_member:
            if not is_chosen:
                types[chosen_metadata_object] = self.package[chosen_metadata_object]
            else:
                if len(types[chosen_metadata_object]) != len(self.package[chosen_metadata_object]):
                    types[chosen_metadata_object] = self.package[chosen_metadata_object]
                else:
                    del types[chosen_metadata_object]
        elif chosen_metadata_object in types:
            if not is_chosen:
                if chosen_member not in types[chosen_metadata_object]:
                    types[chosen_metadata_object].append(chosen_member)
            else:
                types[chosen_metadata_object].remove(chosen_member)
        else:
            types[chosen_metadata_object] = [chosen_member]

        view.settings().set("types", types)

        # Build package.xml content
        metadata_objects = []
        for _type in types:
            metadata_objects.append(
                "<types>%s<name>%s</name></types>" % (
                    "".join(["<members>%s</members>" % m for m in types[_type]]),
                    _type
                )
            )

        self.package_xml_content = """<?xml version="1.0" encoding="UTF-8"?>
            <Package xmlns="http://soap.sforce.com/2006/04/metadata">
                {metadata_objects}
                <version>{api_version}.0</version>
            </Package>
        """.format(
            metadata_objects="".join(metadata_objects),
            api_version=self.settings["api_version"]
        )

        view.run_command("new_dynamic_view", {
            "view_name": "package.xml",
            "erase_all": True,
            "input": util.format_xml(self.package_xml_content).decode("UTF-8")
        })

        sublime.set_timeout(lambda:self.window.run_command("build_package_xml"), 10)

    def is_enabled(self):
        self.settings = context.get_settings()
        cache = os.path.join(self.settings["workspace"], ".config", "metadata.json")
        return os.path.isfile(cache)

class CreatePackageXml(sublime_plugin.WindowCommand):
    def __init__(self, *args, **kwargs):
        super(CreatePackageXml, self).__init__(*args, **kwargs)

    def run(self, dirs):
        _dir = dirs[0]
        settings = context.get_settings()
        package_xml_content = """<?xml version="1.0" encoding="UTF-8"?>
            <Package xmlns="http://soap.sforce.com/2006/04/metadata">
                <types>
                    <members>*</members>
                    <name>ApexClass</name>
                </types>
                <version>{0}.0</version>
            </Package>
        """.format(settings["api_version"])
        file_name = os.path.join(_dir, "package.xml")
        if os.path.isfile(file_name):
            message = "Package.xml is already exist, override?"
            if not sublime.ok_cancel_dialog(message, "Override?"):
                return

        with open(file_name, "wb") as fp:
            fp.write(util.format_xml(package_xml_content))

        # If created succeed, just open it
        sublime.active_window().open_file(file_name)

    def is_visible(self, dirs):
        if not dirs or len(dirs) > 1: return False
        return True

class RetrievePackageXml(sublime_plugin.WindowCommand):
    def __init__(self, *args, **kwargs):
        super(RetrievePackageXml, self).__init__(*args, **kwargs)

    def run(self, files=None):
        # Build types
        try:
            with open(self._file, "rb") as fp:
                content = fp.read()
            self.types = util.build_package_types(content)
        except Exception as ex:
            Printer.get('error').write(str(ex))
            return

        # Initiate extract_to
        path, name = os.path.split(self._file)
        name, ext = name.split(".")
        time_stamp = time.strftime("%Y%m%d%H%M", time.localtime(time.time()))
        settings = context.get_settings()
        project_name = settings["default_project_name"]
        extract_to = os.path.join(path, "%s-%s-%s" % (
            project_name, name, time_stamp
        ))

        sublime.active_window().show_input_panel("Input ExtractedTo Path", 
            extract_to, self.on_input_extractto, None, None)

    def on_input_extractto(self, extract_to):
        # Check input
        if not extract_to or not os.path.isabs(extract_to):
            message = 'Invalid path, do you want to try again?'
            if not sublime.ok_cancel_dialog(message, "Try Again?"): return
            self.window.show_input_panel("Please Input Name: ", "", 
                self.on_input_extractto, None, None)
            return

        # Start retrieve
        processor.handle_retrieve_package(self.types, extract_to)

    def is_visible(self, files=None):
        self._file = None
        
        if files and len(files) > 1: 
            return False
        elif files and len(files) == 1:
            # Invoked from sidebar menu
            self._file = files[0]
        else:
            # Invoked from context menu
            view = sublime.active_window().active_view()
            self._file = view.file_name()

        if not self._file or not self._file.endswith(".xml"):
            return False

        return True