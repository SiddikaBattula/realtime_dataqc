import pymysql
from config import Config
from logger import get_logger
import time

log = get_logger(__name__)

class MySQLClient:

    def __init__(
        self,
        ip_address,
        database_name
    ):

        self.connection = pymysql.connect(
            host=ip_address,
            user=Config.DB_USERNAME,
            password=Config.DB_PASSWORD,
            port=Config.DB_PORT,
            database=database_name,
            charset="utf8",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True
        )

    def get_current_row(self):

        query = f"""
        SELECT *
        FROM {Config.TABLE_NAME}
        LIMIT 1
        """

        with self.connection.cursor() as cursor:
            cursor.execute(query)
            return cursor.fetchone()

    def save_alert(self, alert_time, description):

        query = """
        INSERT INTO dataqcalert
        (
            Time,
            Description
        )
        VALUES
        (
            %s,
            %s
        )
        """

        with self.connection.cursor() as cursor:

            cursor.execute(
                query,
                (
                    alert_time,
                    description
                )
            )

