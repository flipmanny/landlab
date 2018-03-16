#! /usr/bin/env python
"""

"""
import numpy as np
from six import string_types

from pandas import DataFrame

from landlab.field import GroupError

_LOCATIONS = {'node': 'number_of_nodes',
              'patch': 'number_of_patches',
              'link': 'number_of_links',
              'corner': 'number_of_corners',
              'face': 'number_of_faces',
              'cell': 'number_of_cells'}


class ItemCollection(DataFrame):
    """

    Examples
    --------
    
    """
    _metadata = ['number_of_items', 'permitted_locations', '_grid']
    
    @property
    def _constructor(self):
        return ItemCollection

    def __init__(self, grid, data=None, grid_element=None, element_id=None):
        """
        """
        # save a reference to the grid
        self._grid = grid
        
        # Get the locations that are permitted on this grid.
        permitted_locations = []
        for loc in _LOCATIONS:
            try:
                grid.keys(loc)
                permitted_locations.append(loc)
            except GroupError:
                pass

        self.permitted_locations = permitted_locations
        
        
        # get the number of elements in the dataset:
        num_items = []
        for dat in data.keys():
            # check the size of all parts of data, either length 1 or
            # length num_items
            ni = len(data[dat])
            num_items.append(ni)

        if np.all(num_items[0] == np.array(num_items)):
            self.number_of_items = num_items[0]
        else:
            raise ValueError(('Data passed to ItemCollection must be '
                              ' the same length.'))
        
        # make sure that grid element is of a permitted type and the correct size.
        if isinstance(grid_element, string_types):
            if grid_element in permitted_locations:
                pass
            else:
                raise ValueError(('Location index provided: ' + grid_element +
                                  ' is not a permitted location for this grid '
                                  'type.'))
            ge_name = grid_element
            grid_element = np.empty((self.number_of_items, ), dtype=object)
            grid_element.fill(ge_name)
            
        else:
            for loc in grid_element:
                if loc in permitted_locations:
                    pass
                else:
                    raise ValueError(('Location index provided: ' + loc + ' is not'
                                     ' a permitted location for this grid type.'))


        if len(grid_element) != self.number_of_items:
            raise ValueError(('grid_element passed to ItemCollection must be '
                              ' the same length as the data or 1.'))
            
        if len(element_id) != self.number_of_items:
            raise ValueError(('element_id passed to ItemCollection must be '
                              ' the same length as the data.'))

        # add grid element and element ID to data frame
        data['grid_element'] = grid_element
        data['element_id'] = element_id
        
        # initialized the PD dataframe now that we've done checks
        super(ItemCollection, self).__init__(data=data)

        # check that element IDs do not exceed number of elements on this grid
        self.check_element_id_values()

    def check_element_id_values(self):
        """ """
        for loc in self.permitted_locations:
            
            max_size = self._grid[loc].size
            
            selected_elements = self.loc[self['grid_element'] == loc, 'element_id']
            
            if selected_elements.size > 0:
                if max(selected_elements) > max_size:
                    raise ValueError('An item residing at ' + loc + ' has an '
                                     'element_id larger than the size of this '
                                     'part of the grid.')
                    
    def add_variables(self, variable, values):
        """ """
        pass
    
    def add_items(self, data):
        """ """
        pass
    
    def sum(self, var, at='node'):
        """ """
        pass

    def mean(self, var, at='node'):
        """ """
        pass

    def min(self, var, at='node'):
        """ """
        pass

    def max(self, var, at='node'):
        """ """
        pass

    def median(self, var, at='node'):
        """ """
        pass
