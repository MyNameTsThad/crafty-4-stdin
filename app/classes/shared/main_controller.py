import os
import pathlib
from pathlib import Path
import shutil
import time
import logging
import tempfile
from typing import Union
from peewee import DoesNotExist

# TZLocal is set as a hidden import on win pipeline
from tzlocal import get_localzone
from apscheduler.schedulers.background import BackgroundScheduler

from app.classes.controllers.crafty_perms_controller import Crafty_Perms_Controller
from app.classes.controllers.management_controller import Management_Controller
from app.classes.controllers.users_controller import Users_Controller
from app.classes.controllers.roles_controller import Roles_Controller
from app.classes.controllers.server_perms_controller import Server_Perms_Controller
from app.classes.controllers.servers_controller import Servers_Controller
from app.classes.models.server_permissions import Enum_Permissions_Server
from app.classes.models.users import helper_users
from app.classes.models.roles import helper_roles
from app.classes.models.management import helpers_management
from app.classes.models.servers import helper_servers
from app.classes.shared.authentication import Authentication
from app.classes.shared.console import Console
from app.classes.shared.helpers import Helpers
from app.classes.shared.server import Server
from app.classes.shared.file_helpers import FileHelpers
from app.classes.minecraft.server_props import ServerProps
from app.classes.minecraft.serverjars import ServerJars
from app.classes.minecraft.stats import Stats

logger = logging.getLogger(__name__)


class Controller:
    def __init__(self, database, helper):
        self.helper = helper
        self.server_jars = ServerJars(helper)
        self.users_helper = helper_users(database, self.helper)
        self.roles_helper = helper_roles(database)
        self.servers_helper = helper_servers(database)
        self.management_helper = helpers_management(database, self.helper)
        self.authentication = Authentication(self.helper)
        self.servers_list = []
        self.stats = Stats(self.helper, self)
        self.crafty_perms = Crafty_Perms_Controller()
        self.management = Management_Controller(self.management_helper)
        self.roles = Roles_Controller(self.users_helper, self.roles_helper)
        self.server_perms = Server_Perms_Controller()
        self.servers = Servers_Controller(self.servers_helper)
        self.users = Users_Controller(
            self.helper, self.users_helper, self.authentication
        )
        tz = get_localzone()
        self.support_scheduler = BackgroundScheduler(timezone=str(tz))
        self.support_scheduler.start()

    def check_server_loaded(self, server_id_to_check: int):

        logger.info(f"Checking to see if we already registered {server_id_to_check}")

        for s in self.servers_list:
            known_server = s.get("server_id")
            if known_server is None:
                return False

            if known_server == server_id_to_check:
                logger.info(
                    f"skipping initialization of server {server_id_to_check} "
                    f"because it is already loaded"
                )
                return True

        return False

    def init_all_servers(self):

        servers = self.servers.get_all_defined_servers()

        for s in servers:
            server_id = s.get("server_id")

            # if we have already initialized this server, let's skip it.
            if self.check_server_loaded(server_id):
                continue

            # if this server path no longer exists - let's warn and bomb out
            if not Helpers.check_path_exists(
                Helpers.get_os_understandable_path(s["path"])
            ):
                logger.warning(
                    f"Unable to find server {s['server_name']} at path {s['path']}. "
                    f"Skipping this server"
                )

                Console.warning(
                    f"Unable to find server {s['server_name']} at path {s['path']}. "
                    f"Skipping this server"
                )
                continue

            settings_file = os.path.join(
                Helpers.get_os_understandable_path(s["path"]), "server.properties"
            )

            # if the properties file isn't there, let's warn
            if not Helpers.check_file_exists(settings_file):
                logger.error(f"Unable to find {settings_file}. Skipping this server.")
                Console.error(f"Unable to find {settings_file}. Skipping this server.")
                continue

            settings = ServerProps(settings_file)

            temp_server_dict = {
                "server_id": s.get("server_id"),
                "server_data_obj": s,
                "server_obj": Server(self.helper, self.management_helper, self.stats),
                "server_settings": settings.props,
            }

            # setup the server, do the auto start and all that jazz
            temp_server_dict["server_obj"].do_server_setup(s)

            # add this temp object to the list of init servers
            self.servers_list.append(temp_server_dict)

            if s["auto_start"]:
                self.servers.set_waiting_start(s["server_id"], True)

            self.refresh_server_settings(s["server_id"])

            Console.info(
                f"Loaded Server: ID {s['server_id']}"
                + f" | Name: {s['server_name']}"
                + f" | Autostart: {s['auto_start']}"
                + f" | Delay: {s['auto_start_delay']} "
            )

    def refresh_server_settings(self, server_id: int):
        server_obj = self.get_server_obj(server_id)
        server_obj.reload_server_settings()

    @staticmethod
    def check_system_user():
        if helper_users.get_user_id_by_name("system") is not None:
            return True
        else:
            return False

    def set_project_root(self, root_dir):
        self.project_root = root_dir

    def package_support_logs(self, exec_user):
        if exec_user["preparing"]:
            return
        self.users.set_prepare(exec_user["user_id"])
        # pausing so on screen notifications can run for user
        time.sleep(7)
        self.helper.websocket_helper.broadcast_user(
            exec_user["user_id"], "notification", "Preparing your support logs"
        )
        tempDir = tempfile.mkdtemp()
        tempZipStorage = tempfile.mkdtemp()
        full_temp = os.path.join(tempDir, "support_logs")
        os.mkdir(full_temp)
        tempZipStorage = os.path.join(tempZipStorage, "support_logs")
        crafty_path = os.path.join(full_temp, "crafty")
        os.mkdir(crafty_path)
        server_path = os.path.join(full_temp, "server")
        os.mkdir(server_path)
        if exec_user["superuser"]:
            auth_servers = self.servers.get_all_defined_servers()
        else:
            user_servers = self.servers.get_authorized_servers(
                int(exec_user["user_id"])
            )
            auth_servers = []
            for server in user_servers:
                if (
                    Enum_Permissions_Server.Logs
                    in self.server_perms.get_user_id_permissions_list(
                        exec_user["user_id"], server["server_id"]
                    )
                ):
                    auth_servers.append(server)
                else:
                    logger.info(
                        f"Logs permission not available for server "
                        f"{server['server_name']}. Skipping."
                    )
        # we'll iterate through our list of log paths from auth servers.
        for server in auth_servers:
            final_path = os.path.join(server_path, str(server["server_name"]))
            try:
                os.mkdir(final_path)
            except FileExistsError:
                final_path += "_" + server["server_uuid"]
                os.mkdir(final_path)
            try:
                FileHelpers.copy_file(server["log_path"], final_path)
            except Exception as e:
                logger.warning(f"Failed to copy file with error: {e}")
        # Copy crafty logs to archive dir
        full_log_name = os.path.join(crafty_path, "logs")
        FileHelpers.copy_dir(os.path.join(self.project_root, "logs"), full_log_name)
        self.support_scheduler.add_job(
            self.log_status,
            "interval",
            seconds=1,
            id="logs_" + str(exec_user["user_id"]),
            args=[full_temp, tempZipStorage + ".zip", exec_user],
        )
        FileHelpers.make_archive(tempZipStorage, tempDir)

        if len(self.helper.websocket_helper.clients) > 0:
            self.helper.websocket_helper.broadcast_user(
                exec_user["user_id"],
                "support_status_update",
                Helpers.calc_percent(full_temp, tempZipStorage + ".zip"),
            )

        tempZipStorage += ".zip"
        self.helper.websocket_helper.broadcast_user(
            exec_user["user_id"], "send_logs_bootbox", {}
        )

        self.users.set_support_path(exec_user["user_id"], tempZipStorage)

        self.users.stop_prepare(exec_user["user_id"])
        self.support_scheduler.remove_job("logs_" + str(exec_user["user_id"]))

    @staticmethod
    def add_system_user():
        helper_users.add_user(
            "system",
            Helpers.random_string_generator(64),
            "default@example.com",
            False,
            False,
        )

    def get_server_settings(self, server_id):
        for s in self.servers_list:
            if int(s["server_id"]) == int(server_id):
                return s["server_settings"]

        logger.warning(f"Unable to find server object for server id {server_id}")
        return False

    def crash_detection(self, server_obj):
        svr = self.get_server_obj(server_obj.server_id)
        # start or stop crash detection depending upon user preference
        # The below functions check to see if the server is running.
        # They only execute if it's running.
        if server_obj.crash_detection == 1:
            svr.start_crash_detection()
        else:
            svr.stop_crash_detection()

    def log_status(self, source_path, dest_path, exec_user):
        results = Helpers.calc_percent(source_path, dest_path)
        self.log_stats = results

        if len(self.helper.websocket_helper.clients) > 0:
            self.helper.websocket_helper.broadcast_user(
                exec_user["user_id"], "support_status_update", results
            )

    def send_log_status(self):
        try:
            return self.log_stats
        except:
            return {"percent": 0, "total_files": 0}

    def get_server_obj(self, server_id: Union[str, int]) -> Union[bool, Server]:
        for s in self.servers_list:
            if str(s["server_id"]) == str(server_id):
                return s["server_obj"]

        logger.warning(f"Unable to find server object for server id {server_id}")
        return False  # TODO: Change to None

    def get_server_data(self, server_id: str):
        for s in self.servers_list:
            if str(s["server_id"]) == str(server_id):
                return s["server_data_obj"]

        logger.warning(f"Unable to find server object for server id {server_id}")
        return False

    @staticmethod
    def list_defined_servers():
        servers = helper_servers.get_all_defined_servers()
        return servers

    def list_running_servers(self):
        running_servers = []

        # for each server
        for s in self.servers_list:

            # is the server running?
            srv_obj = s["server_obj"]
            running = srv_obj.check_running()
            # if so, let's add a dictionary to the list of running servers
            if running:
                running_servers.append({"id": srv_obj.server_id, "name": srv_obj.name})

        return running_servers

    def stop_all_servers(self):
        servers = self.list_running_servers()
        logger.info(f"Found {len(servers)} running server(s)")
        Console.info(f"Found {len(servers)} running server(s)")

        logger.info("Stopping All Servers")
        Console.info("Stopping All Servers")

        for s in servers:
            logger.info(f"Stopping Server ID {s['id']} - {s['name']}")
            Console.info(f"Stopping Server ID {s['id']} - {s['name']}")

            self.stop_server(s["id"])

            # let's wait 2 seconds to let everything flush out
            time.sleep(2)

        logger.info("All Servers Stopped")
        Console.info("All Servers Stopped")

    def stop_server(self, server_id):
        # issue the stop command
        svr_obj = self.get_server_obj(server_id)
        svr_obj.stop_threaded_server()

    def create_jar_server(
        self,
        server: str,
        version: str,
        name: str,
        min_mem: int,
        max_mem: int,
        port: int,
    ):
        server_id = Helpers.create_uuid()
        server_dir = os.path.join(self.helper.servers_dir, server_id)
        backup_path = os.path.join(self.helper.backup_path, server_id)
        if Helpers.is_os_windows():
            server_dir = Helpers.wtol_path(server_dir)
            backup_path = Helpers.wtol_path(backup_path)
            server_dir.replace(" ", "^ ")
            backup_path.replace(" ", "^ ")

        server_file = f"{server}-{version}.jar"
        full_jar_path = os.path.join(server_dir, server_file)

        # make the dir - perhaps a UUID?
        Helpers.ensure_dir_exists(server_dir)
        Helpers.ensure_dir_exists(backup_path)

        try:
            # do a eula.txt
            with open(os.path.join(server_dir, "eula.txt"), "w", encoding="utf-8") as f:
                f.write("eula=false")
                f.close()

            # setup server.properties with the port
            with open(
                os.path.join(server_dir, "server.properties"), "w", encoding="utf-8"
            ) as f:
                f.write(f"server-port={port}")
                f.close()

        except Exception as e:
            logger.error(f"Unable to create required server files due to :{e}")
            return False

        if Helpers.is_os_windows():
            server_command = (
                f"java -Xms{Helpers.float_to_string(min_mem)}M "
                f"-Xmx{Helpers.float_to_string(max_mem)}M "
                f'-jar "{full_jar_path}" nogui'
            )
        else:
            server_command = (
                f"java -Xms{Helpers.float_to_string(min_mem)}M "
                f"-Xmx{Helpers.float_to_string(max_mem)}M "
                f"-jar {full_jar_path} nogui"
            )
        server_log_file = f"{server_dir}/logs/latest.log"
        server_stop = "stop"

        new_id = self.register_server(
            name,
            server_id,
            server_dir,
            backup_path,
            server_command,
            server_file,
            server_log_file,
            server_stop,
            port,
            server_type="minecraft-java",
        )

        # download the jar
        self.server_jars.download_jar(server, version, full_jar_path, new_id)

        return new_id

    @staticmethod
    def verify_jar_server(server_path: str, server_jar: str):
        server_path = Helpers.get_os_understandable_path(server_path)
        path_check = Helpers.check_path_exists(server_path)
        jar_check = Helpers.check_file_exists(os.path.join(server_path, server_jar))
        if not path_check or not jar_check:
            return False
        return True

    @staticmethod
    def verify_zip_server(zip_path: str):
        zip_path = Helpers.get_os_understandable_path(zip_path)
        zip_check = Helpers.check_file_exists(zip_path)
        if not zip_check:
            return False
        return True

    def import_jar_server(
        self,
        server_name: str,
        server_path: str,
        server_jar: str,
        min_mem: int,
        max_mem: int,
        port: int,
    ):
        server_id = Helpers.create_uuid()
        new_server_dir = os.path.join(Helpers.servers_dir, server_id)
        backup_path = os.path.join(Helpers.backup_path, server_id)
        if Helpers.is_os_windows():
            new_server_dir = Helpers.wtol_path(new_server_dir)
            backup_path = Helpers.wtol_path(backup_path)
            new_server_dir.replace(" ", "^ ")
            backup_path.replace(" ", "^ ")

        Helpers.ensure_dir_exists(new_server_dir)
        Helpers.ensure_dir_exists(backup_path)
        server_path = Helpers.get_os_understandable_path(server_path)
        try:
            FileHelpers.copy_dir(server_path, new_server_dir, True)
        except shutil.Error as ex:
            logger.error(f"Server import failed with error: {ex}")

        has_properties = False
        for item in os.listdir(new_server_dir):
            if str(item) == "server.properties":
                has_properties = True
        if not has_properties:
            logger.info(
                f"No server.properties found on zip file import. "
                f"Creating one with port selection of {str(port)}"
            )
            with open(
                os.path.join(new_server_dir, "server.properties"), "w", encoding="utf-8"
            ) as f:
                f.write(f"server-port={port}")
                f.close()

        full_jar_path = os.path.join(new_server_dir, server_jar)

        if Helpers.is_os_windows():
            server_command = (
                f"java -Xms{Helpers.float_to_string(min_mem)}M "
                f"-Xmx{Helpers.float_to_string(max_mem)}M "
                f'-jar "{full_jar_path}" nogui'
            )
        else:
            server_command = (
                f"java -Xms{Helpers.float_to_string(min_mem)}M "
                f"-Xmx{Helpers.float_to_string(max_mem)}M "
                f"-jar {full_jar_path} nogui"
            )
        server_log_file = f"{new_server_dir}/logs/latest.log"
        server_stop = "stop"

        new_id = self.register_server(
            server_name,
            server_id,
            new_server_dir,
            backup_path,
            server_command,
            server_jar,
            server_log_file,
            server_stop,
            port,
            server_type="minecraft-java",
        )
        return new_id

    def import_zip_server(
        self,
        server_name: str,
        zip_path: str,
        server_jar: str,
        min_mem: int,
        max_mem: int,
        port: int,
    ):
        server_id = Helpers.create_uuid()
        new_server_dir = os.path.join(self.helper.servers_dir, server_id)
        backup_path = os.path.join(self.helper.backup_path, server_id)
        if Helpers.is_os_windows():
            new_server_dir = Helpers.wtol_path(new_server_dir)
            backup_path = Helpers.wtol_path(backup_path)
            new_server_dir.replace(" ", "^ ")
            backup_path.replace(" ", "^ ")

        tempDir = Helpers.get_os_understandable_path(zip_path)
        Helpers.ensure_dir_exists(new_server_dir)
        Helpers.ensure_dir_exists(backup_path)
        has_properties = False
        # extracts archive to temp directory
        for item in os.listdir(tempDir):
            if str(item) == "server.properties":
                has_properties = True
            try:
                if not os.path.isdir(os.path.join(tempDir, item)):
                    FileHelpers.move_file(
                        os.path.join(tempDir, item), os.path.join(new_server_dir, item)
                    )
                else:
                    FileHelpers.move_dir(
                        os.path.join(tempDir, item), os.path.join(new_server_dir, item)
                    )
            except Exception as ex:
                logger.error(f"ERROR IN ZIP IMPORT: {ex}")
        if not has_properties:
            logger.info(
                f"No server.properties found on zip file import. "
                f"Creating one with port selection of {str(port)}"
            )
            with open(
                os.path.join(new_server_dir, "server.properties"), "w", encoding="utf-8"
            ) as f:
                f.write(f"server-port={port}")
                f.close()

        full_jar_path = os.path.join(new_server_dir, server_jar)

        if Helpers.is_os_windows():
            server_command = (
                f"java -Xms{Helpers.float_to_string(min_mem)}M "
                f"-Xmx{Helpers.float_to_string(max_mem)}M "
                f'-jar "{full_jar_path}" nogui'
            )
        else:
            server_command = (
                f"java -Xms{Helpers.float_to_string(min_mem)}M "
                f"-Xmx{Helpers.float_to_string(max_mem)}M "
                f"-jar {full_jar_path} nogui"
            )
        logger.debug("command: " + server_command)
        server_log_file = f"{new_server_dir}/logs/latest.log"
        server_stop = "stop"

        new_id = self.register_server(
            server_name,
            server_id,
            new_server_dir,
            backup_path,
            server_command,
            server_jar,
            server_log_file,
            server_stop,
            port,
            server_type="minecraft-java",
        )
        return new_id

    # **********************************************************************************
    #                                   BEDROCK IMPORTS
    # **********************************************************************************

    def import_bedrock_server(
        self, server_name: str, server_path: str, server_exe: str, port: int
    ):
        server_id = Helpers.create_uuid()
        new_server_dir = os.path.join(self.helper.servers_dir, server_id)
        backup_path = os.path.join(self.helper.backup_path, server_id)
        if Helpers.is_os_windows():
            new_server_dir = Helpers.wtol_path(new_server_dir)
            backup_path = Helpers.wtol_path(backup_path)
            new_server_dir.replace(" ", "^ ")
            backup_path.replace(" ", "^ ")

        Helpers.ensure_dir_exists(new_server_dir)
        Helpers.ensure_dir_exists(backup_path)
        server_path = Helpers.get_os_understandable_path(server_path)
        try:
            FileHelpers.copy_dir(server_path, new_server_dir, True)
        except shutil.Error as ex:
            logger.error(f"Server import failed with error: {ex}")

        has_properties = False
        for item in os.listdir(new_server_dir):
            if str(item) == "server.properties":
                has_properties = True
        if not has_properties:
            logger.info(
                f"No server.properties found on zip file import. "
                f"Creating one with port selection of {str(port)}"
            )
            with open(
                os.path.join(new_server_dir, "server.properties"), "w", encoding="utf-8"
            ) as f:
                f.write(f"server-port={port}")
                f.close()

        full_jar_path = os.path.join(new_server_dir, server_exe)

        if Helpers.is_os_windows():
            server_command = f'"{full_jar_path}"'
        else:
            server_command = f"./{server_exe}"
        logger.debug("command: " + server_command)
        server_log_file = "N/A"
        server_stop = "stop"

        new_id = self.register_server(
            server_name,
            server_id,
            new_server_dir,
            backup_path,
            server_command,
            server_exe,
            server_log_file,
            server_stop,
            port,
            server_type="minecraft-bedrock",
        )
        if os.name != "nt":
            if Helpers.check_file_exists(full_jar_path):
                os.chmod(full_jar_path, 0o2775)
        return new_id

    def import_bedrock_zip_server(
        self, server_name: str, zip_path: str, server_exe: str, port: int
    ):
        server_id = Helpers.create_uuid()
        new_server_dir = os.path.join(self.helper.servers_dir, server_id)
        backup_path = os.path.join(self.helper.backup_path, server_id)
        if Helpers.is_os_windows():
            new_server_dir = Helpers.wtol_path(new_server_dir)
            backup_path = Helpers.wtol_path(backup_path)
            new_server_dir.replace(" ", "^ ")
            backup_path.replace(" ", "^ ")

        tempDir = Helpers.get_os_understandable_path(zip_path)
        Helpers.ensure_dir_exists(new_server_dir)
        Helpers.ensure_dir_exists(backup_path)
        has_properties = False
        # extracts archive to temp directory
        for item in os.listdir(tempDir):
            if str(item) == "server.properties":
                has_properties = True
            try:
                if not os.path.isdir(os.path.join(tempDir, item)):
                    FileHelpers.move_file(
                        os.path.join(tempDir, item), os.path.join(new_server_dir, item)
                    )
                else:
                    FileHelpers.move_dir(
                        os.path.join(tempDir, item), os.path.join(new_server_dir, item)
                    )
            except Exception as ex:
                logger.error(f"ERROR IN ZIP IMPORT: {ex}")
        if not has_properties:
            logger.info(
                f"No server.properties found on zip file import. "
                f"Creating one with port selection of {str(port)}"
            )
            with open(
                os.path.join(new_server_dir, "server.properties"), "w", encoding="utf-8"
            ) as f:
                f.write(f"server-port={port}")
                f.close()

        full_jar_path = os.path.join(new_server_dir, server_exe)

        if Helpers.is_os_windows():
            server_command = f'"{full_jar_path}"'
        else:
            server_command = f"./{server_exe}"
        logger.debug("command: " + server_command)
        server_log_file = "N/A"
        server_stop = "stop"

        new_id = self.register_server(
            server_name,
            server_id,
            new_server_dir,
            backup_path,
            server_command,
            server_exe,
            server_log_file,
            server_stop,
            port,
            server_type="minecraft-bedrock",
        )
        if os.name != "nt":
            if Helpers.check_file_exists(full_jar_path):
                os.chmod(full_jar_path, 0o2775)

        return new_id

    # **********************************************************************************
    #                                   BEDROCK IMPORTS END
    # **********************************************************************************

    def rename_backup_dir(self, old_server_id, new_server_id, new_uuid):
        server_data = self.servers.get_server_data_by_id(old_server_id)
        old_bu_path = server_data["backup_path"]
        Server_Perms_Controller.backup_role_swap(old_server_id, new_server_id)
        if not Helpers.is_os_windows():
            backup_path = Helpers.validate_traversal(
                self.helper.backup_path, old_bu_path
            )
        if Helpers.is_os_windows():
            backup_path = Helpers.validate_traversal(
                Helpers.wtol_path(self.helper.backup_path),
                Helpers.wtol_path(old_bu_path),
            )
            backup_path = Helpers.wtol_path(str(backup_path))
            backup_path.replace(" ", "^ ")
            backup_path = Path(backup_path)
        backup_path_components = list(backup_path.parts)
        backup_path_components[-1] = new_uuid
        new_bu_path = pathlib.PurePath(os.path.join(*backup_path_components))
        if os.path.isdir(new_bu_path):
            if Helpers.validate_traversal(self.helper.backup_path, new_bu_path):
                os.rmdir(new_bu_path)
        backup_path.rename(new_bu_path)

    def register_server(
        self,
        name: str,
        server_uuid: str,
        server_dir: str,
        backup_path: str,
        server_command: str,
        server_file: str,
        server_log_file: str,
        server_stop: str,
        server_port: int,
        server_type: str,
    ):
        # put data in the db

        new_id = self.servers.create_server(
            name,
            server_uuid,
            server_dir,
            backup_path,
            server_command,
            server_file,
            server_log_file,
            server_stop,
            server_type,
            server_port,
        )

        if not Helpers.check_file_exists(
            os.path.join(server_dir, "crafty_managed.txt")
        ):
            try:
                # place a file in the dir saying it's owned by crafty
                with open(
                    os.path.join(server_dir, "crafty_managed.txt"),
                    "w",
                    encoding="utf-8",
                ) as f:
                    f.write(
                        "The server is managed by Crafty Controller.\n "
                        "Leave this directory/files alone please"
                    )
                    f.close()

            except Exception as e:
                logger.error(f"Unable to create required server files due to :{e}")
                return False

        # let's re-init all servers
        self.init_all_servers()

        return new_id

    def remove_server(self, server_id, files):
        counter = 0
        for s in self.servers_list:

            # if this is the droid... im mean server we are looking for...
            if str(s["server_id"]) == str(server_id):
                server_data = self.get_server_data(server_id)
                server_name = server_data["server_name"]

                logger.info(f"Deleting Server: ID {server_id} | Name: {server_name} ")
                Console.info(f"Deleting Server: ID {server_id} | Name: {server_name} ")

                srv_obj = s["server_obj"]
                running = srv_obj.check_running()

                if running:
                    self.stop_server(server_id)
                if files:
                    try:
                        FileHelpers.del_dirs(
                            Helpers.get_os_understandable_path(
                                self.servers.get_server_data_by_id(server_id)["path"]
                            )
                        )
                    except Exception as e:
                        logger.error(
                            f"Unable to delete server files for server with ID: "
                            f"{server_id} with error logged: {e}"
                        )
                    if Helpers.check_path_exists(
                        self.servers.get_server_data_by_id(server_id)["backup_path"]
                    ):
                        FileHelpers.del_dirs(
                            Helpers.get_os_understandable_path(
                                self.servers.get_server_data_by_id(server_id)[
                                    "backup_path"
                                ]
                            )
                        )

                # Cleanup scheduled tasks
                try:
                    helpers_management.delete_scheduled_task_by_server(server_id)
                except DoesNotExist:
                    logger.info("No scheduled jobs exist. Continuing.")
                # remove the server from the DB
                self.servers.remove_server(server_id)

                # remove the server from servers list
                self.servers_list.pop(counter)

            counter += 1

    @staticmethod
    def clear_unexecuted_commands():
        helpers_management.clear_unexecuted_commands()

    @staticmethod
    def clear_support_status():
        helper_users.clear_support_status()
