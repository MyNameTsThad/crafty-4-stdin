# Generated by database migrator
import peewee

def migrate(migrator, database, **kwargs):
    migrator.add_columns('schedules', one_time=peewee.BooleanField(default=False))
    """
    Write your migrations here.
    """



def rollback(migrator, database, **kwargs):
    migrator.drop_columns('schedules', ['one_time']) 
    """
    Write your rollback migrations here.
    """
