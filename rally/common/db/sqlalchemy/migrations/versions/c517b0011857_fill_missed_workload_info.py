# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""fill_missed_workload_info

Revision ID: c517b0011857
Revises: 35fe16d4ab1c
Create Date: 2017-06-22 18:46:09.281312

"""

import collections

from alembic import op
import sqlalchemy as sa

from rally.common.db.sqlalchemy import types as sa_types
from rally import exceptions
from rally.task import atomic
from rally.task.processing import charts

# revision identifiers, used by Alembic.
revision = "c517b0011857"
down_revision = "35fe16d4ab1c"
branch_labels = None
depends_on = None


workload_helper = sa.Table(
    "workloads",
    sa.MetaData(),
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("uuid", sa.String(36), nullable=False),
    sa.Column("start_time", sa_types.TimeStamp),
    sa.Column("statistics", sa_types.MutableJSONEncodedDict, default={},
              nullable=False),
)

workload_data_helper = sa.Table(
    "workloaddata",
    sa.MetaData(),
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("uuid", sa.String(36), nullable=False),
    sa.Column("workload_uuid", sa.String(length=36), nullable=False),
    sa.Column("chunk_data", sa_types.MutableJSONEncodedDict(), nullable=False)
)


def upgrade():
    connection = op.get_bind()

    for workload in connection.execute(workload_helper.select()):
        full_data = []
        for wdata in connection.execute(workload_data_helper.select(
                workload_data_helper.c.workload_uuid == workload.uuid)):
            chunk_data = wdata.chunk_data["raw"]

            require_updating = False
            for itr in chunk_data:
                if "output" not in itr:
                    itr["output"] = {"additive": [], "complete": []}
                    if ("scenario_output" in itr
                            and itr["scenario_output"]["data"]):
                        items = list(itr["scenario_output"]["data"].items())
                        itr["output"]["additive"].append(
                            {"items": items,
                             "title": "Scenario output",
                             "description": "",
                             "chart": "OutputStackedAreaChart"})
                        del itr["scenario_output"]
                    require_updating = True
                if isinstance(itr["atomic_actions"], dict):
                    new_atomic_actions = []
                    started_at = itr["timestamp"]
                    for name, d in itr["atomic_actions"].items():
                        finished_at = started_at + d
                        new_atomic_actions.append(
                            {"name": name, "children": [],
                             "started_at": started_at,
                             "finished_at": finished_at})
                        started_at = finished_at
                    itr["atomic_actions"] = new_atomic_actions
                    require_updating = True

            if require_updating:
                connection.execute(workload_data_helper.update().where(
                    workload_data_helper.c.uuid == wdata.uuid).values(
                    chunk_data={"raw": chunk_data}))

            full_data.extend(chunk_data)

        if full_data:
            full_data.sort(key=lambda itr: itr["timestamp"])

            start_time = full_data[0]["timestamp"]

            atomics = collections.OrderedDict()

            for itr in full_data:
                merged_atomic = atomic.merge_atomic(itr["atomic_actions"])
                for name, value in merged_atomic.items():
                    duration = value["duration"]
                    count = value["count"]
                    if name not in atomics or count > atomics[name]["count"]:
                        atomics[name] = {"min_duration": duration,
                                         "max_duration": duration,
                                         "count": count}
                    elif count == atomics[name]["count"]:
                        if duration < atomics[name]["min_duration"]:
                            atomics[name]["min_duration"] = duration
                        if duration > atomics[name]["max_duration"]:
                            atomics[name]["max_duration"] = duration

            durations_stat = charts.MainStatsTable(
                {"total_iteration_count": len(full_data),
                 "statistics": {"atomics": atomics}})

            for itr in full_data:
                durations_stat.add_iteration(itr)

            connection.execute(workload_helper.update().where(
                workload_helper.c.uuid == workload.uuid).values(
                start_time=start_time,
                statistics={"durations": durations_stat.render(),
                            "atomics": atomics}))


def downgrade():
    raise exceptions.DowngradeNotSupported()