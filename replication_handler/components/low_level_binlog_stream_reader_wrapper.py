# -*- coding: utf-8 -*-
import logging

from pymysqlreplication import BinLogStreamReader
from pymysqlreplication.event import GtidEvent
from pymysqlreplication.event import QueryEvent
from pymysqlreplication.row_event import UpdateRowsEvent
from pymysqlreplication.row_event import WriteRowsEvent
from pymysqlreplication.constants.BINLOG import WRITE_ROWS_EVENT_V2
from pymysqlreplication.constants.BINLOG import UPDATE_ROWS_EVENT_V2
from pymysqlreplication.constants.BINLOG import DELETE_ROWS_EVENT_V2

from data_pipeline.message import CreateMessage
from data_pipeline.message import DeleteMessage
from data_pipeline.message import UpdateMessage

from replication_handler import config
from replication_handler.components.base_binlog_stream_reader_wrapper import BaseBinlogStreamReaderWrapper
from replication_handler.util.misc import DataEvent


log = logging.getLogger('replication_handler.components.low_level_binlog_stream_reader_wrapper')


message_type_map = {
    WRITE_ROWS_EVENT_V2: CreateMessage,
    UPDATE_ROWS_EVENT_V2: UpdateMessage,
    DELETE_ROWS_EVENT_V2: DeleteMessage,
}


class LowLevelBinlogStreamReaderWrapper(BaseBinlogStreamReaderWrapper):
    """ This class wraps pymysqlreplication stream object, providing the ability to
    resume stream at a specific position, peek at next event, and pop next event.

    Args:
      position(Position object): use to specify where the stream should resume.
    """

    def __init__(self, position):
        super(LowLevelBinlogStreamReaderWrapper, self).__init__()
        source_config = config.source_database_config.entries[0]
        only_tables = config.env_config.table_whitelist
        connection_config = {
            'host': source_config['host'],
            'port': source_config['port'],
            'user': source_config['user'],
            'passwd': source_config['passwd']
        }
        allowed_event_types = [
            GtidEvent,
            QueryEvent,
            WriteRowsEvent,
            UpdateRowsEvent
        ]

        self._seek(connection_config, allowed_event_types, position, only_tables)

    def _refill_current_events_if_empty(self):
        if not self.current_events:
            self.current_events.extend(self._prepare_event(self.stream.fetchone()))

    def _prepare_event(self, event):
        if isinstance(event, (QueryEvent, GtidEvent)):
            # TODO(cheng|DATAPIPE-173): log_pos and log_file is useful information
            # to have on events, we will decide if we want to remove this when gtid is
            # enabled if the future.
            event.log_pos = self.stream.log_pos
            event.log_file = self.stream.log_file
            return [event]
        else:
            return self._get_data_events_from_row_event(event)

    def _get_data_events_from_row_event(self, row_event):
        """ Convert the rows into events."""
        return [
            DataEvent(
                schema=row_event.schema,
                table=row_event.table,
                log_pos=self.stream.log_pos,
                log_file=self.stream.log_file,
                row=row,
                message_type=message_type_map[row_event.event_type]
            ) for row in row_event.rows
        ]

    def _seek(self, connection_config, allowed_event_types, position, only_tables):
        # server_id doesn't seem to matter but must be set.
        self.stream = BinLogStreamReader(
            connection_settings=connection_config,
            server_id=1,
            only_events=allowed_event_types,
            resume_stream=True,
            only_tables=only_tables,
            **position.to_replication_dict()
        )