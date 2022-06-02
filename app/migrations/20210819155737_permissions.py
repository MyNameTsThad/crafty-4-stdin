# Generated by database migrator
import peewee


def migrate(migrator, database, **kwargs):
    migrator.add_columns(
        "user_servers", permissions=peewee.CharField(default="00000000")
    )  # First argument can be model class OR table name
    migrator.add_columns(
        "role_servers", permissions=peewee.CharField(default="00000000")
    )  # First argument can be model class OR table name
    """
    Write your migrations here.
    """


def rollback(migrator, database, **kwargs):
    migrator.drop_columns(
        "user_servers", ["permissions"]
    )  # First argument can be model class OR table name
    migrator.drop_columns(
        "role_servers", ["permissions"]
    )  # First argument can be model class OR table name
    """
    Write your rollback migrations here.
    """
