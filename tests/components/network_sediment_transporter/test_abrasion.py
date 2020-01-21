import numpy as np
import pytest
from numpy.testing import assert_array_almost_equal, assert_array_equal

from landlab.components import FlowDirectorSteepest, NetworkSedimentTransporter
from landlab.data_record import DataRecord
from landlab.grid.network import NetworkModelGrid

_OUT_OF_NETWORK = NetworkModelGrid.BAD_INDEX - 1


def test_abrasion(
    example_nmg, example_parcels, example_flow_depth, example_flow_director
):
    time = [0.0]  # probably not the sensible way to do this...

    items = {"grid_element": "link", "element_id": np.array([[6], [6]])}

    initial_volume = np.array([[1], [1]])
    abrasion_rate = np.array([0.0001, 0])

    variables = {
        "starting_link": (["item_id"], np.array([6, 6])),
        "abrasion_rate": (["item_id"], abrasion_rate),
        "density": (["item_id"], np.array([2650, 90000])),
        "time_arrival_in_link": (["item_id", "time"], np.array([[0.5], [0]])),
        "active_layer": (["item_id", "time"], np.array([[1], [1]])),
        "location_in_link": (["item_id", "time"], np.array([[0], [0]])),
        "D": (["item_id", "time"], np.array([[0.05], [0.05]])),
        "volume": (["item_id", "time"], initial_volume),
    }

    two_parcels = DataRecord(
        example_nmg,
        items=items,
        time=time,
        data_vars=variables,
        dummy_elements={"link": [_OUT_OF_NETWORK]},
    )

    timesteps = 8

    example_flow_depth = example_flow_depth * 5  # outrageously high transport rate

    nst = NetworkSedimentTransporter(
        example_nmg,
        two_parcels,
        example_flow_director,
        example_flow_depth,
        bed_porosity=0.03,
        g=9.81,
        fluid_density=1000,
        transport_method="WilcockCrowe",
    )

    dt = 60 * 60 * 24  # (seconds) daily timestep

    for t in range(0, (timesteps * dt), dt):
        nst.run_one_step(dt)
        print("Successfully completed a timestep")
        # Need to define original_node_elev after a timestep has passed.
        if t / (60 * 60 * 24) == 1:
            original_parcel_vol = example_parcels.dataset.volume[:, 0][0]

    # Parcel volume should decrease according to abrasion rate
    volume_after_transport = np.squeeze(np.transpose(initial_volume)) * np.exp(
        nst._distance_traveled_cumulative * -abrasion_rate
    )

    # print("volume_after_transport", volume_after_transport)

    assert_array_almost_equal(
        volume_after_transport, two_parcels.dataset.volume[0:2, -1]
    )
