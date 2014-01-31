#! /usr/env/python
"""
This component supercedes an older version, "craters.py".
This component excavates impact craters across a surface. The properties of the
craters broadly follow those observed for the Moon. Craters obey realistic size-
frequency distributions; at the moment this is forced to follow the Shoemaker
scaling, N = k*D^-2.9.
Importantly, this module incorporates a dependence of  ejecta distribution on
impact momentum.
At the moment, this component does not produce complex craters, largely because
it is optimized to run for craters in the meter-km scale. Craters are dug
perpendicular to the local surface, unless this would steepen local slopes so
much as to severely violate mass balance for the crater,or if slopes would
exceed 90 degrees. In other respects, strength effects are not well
incorporated.
As yet, this module does not have a true time interface - a single call of
excavate_a_crater() will dig a single crater, and adjust its size appropriately
for the Shoemaker distribution. It can't yet adjust itself to imposed timesteps
other than this natural timestep.
At the moment, this component requires you to run on a square regular grid.
"""

from random import random
import numpy
from sympy import Symbol
from sympy.solvers import solve
from sympy.utilities.lambdify import lambdify
import pylab

from landlab import ModelParameterDictionary

class impactor(object):
    '''
    This class holds all parameters decribing properties of a single impact
    structure, and contains methods for recalculating fresh and internally 
    consistent data describing such a impact structure.
    Built DEJH Winter 2013, after an earlier version, Spring 2013.
    '''
    
    def __init__(self, grid, input_stream):
        self.grid = grid
        inputs = ModelParameterDictionary(input_stream)
        
        #test the necessary fields are all already present:
        try:
            self.elev = grid.at_node['planet_surface__elevation']
        except:
            print 'elevations not found in grid!'

        #User sets:
        self._minimum_crater = inputs.read_float('min_radius') #km. This is the smallest modelled crater radius. 10m diameter is the strict cutoff for known cr distns
        self._minimum_ejecta_thickness = inputs.read_float('min_ejecta_thickness') #0.00000001
        self._record_impacts_flag = inputs.read_int('record_impacts') #Not actually used; they're recorded by default
        #These parameters are optional
        try:
            self._radius = inputs.read_float('forced_radius')
        except:
            print 'Impact radii will be randomly generated.'
            self.radius_auto_flag = 1
            self.set_cr_radius_from_shoemaker()
        else:
            self.radius_auto_flag = 0
        try:
            self._xcoord = inputs.read_float('x_position')*(grid.get_grid_xdimension()-grid.dx)
            self._ycoord = inputs.read_float('y_position')*(grid.get_grid_ydimension()-grid.dx)
        except:
            print 'Impact sites will be randomly generated.'
            self.position_auto_flag = 1
            self.set_coords()
        else:
            self.position_auto_flag = 0
            self.closest_node_index = grid.snap_coords_to_grid(self._xcoord, self._ycoord)
            self.closest_node_elev = self.elev[self.closest_node_index]
        try:
            self._angle_to_vertical = inputs.read_float('forced_angle')*numpy.pi/180.
            assert self._angle_to_vertical <= 0.5*numpy.pi
        except:
            print 'Impactor angles will be randomly generated.'
            self.angle_auto_flag = 1
            self.set_impactor_angles()
        else:
            #assert 0. <= self._angle_to_vertical <= 90.
            self.angle_auto_flag = 0
            self._azimuth_of_travel = 0.5*numpy.pi
        
        self.cheater_flag = 0

        #The user has no control over the following inbuilt parameters describing crater scaling:
        self.tan_repose = numpy.tan(32.*numpy.pi/180.)
        self._beta_factor = 0.5 #this is the arbitrary term that controls how "stretched out" the ejecta field is. <+0.5 prevents "outside the ejecta field" regions forming
        self._simple_radius_depth_ratio_Pike = 2.55 #Pike thru Holsapple, tho Garvin (2011) gives 2.0 as "typically cited"
                
        self.V = Symbol('V') #Crater cavity vol
        self.r0 = Symbol('r0') #Crater rim radius
        self.T = Symbol('T') #Crater rim ejecta thickness
        self.r = Symbol('r') #actual dist from crater center of a given pt
        self.solution_for_rim_thickness = solve(8./3.*self.T*numpy.pi*self.r0**2 + 0.33333*numpy.pi*self.T*(self.r0**2+(self.r0-self.T/self.tan_repose)**2+self.r0*(self.r0-self.T/self.tan_repose)) - self.V, self.T)
        #...gives a list of 3 sympy expressions, f(V,r), for T
        self.expression_for_local_thickness = self.T*(self.r/self.r0)**-2.75

        #Build the permanent maps for the distances and azimuths between nodes:
        ###The resulting matrix is too big in practical cases (GB of memory use). Need to take another apporach - see set_elevation_change_only_beneath_footprint()
        #print 'Building the distances map for the grid... may take some time...'
        #self.all_node_distances_map, self.all_node_azimuths_map = grid.build_all_node_distances_azimuths_maps()
        #print '...Done.'
        
        self.impact_property_dict = {}
        
        print 'Craters component setup complete!'

    def draw_new_parameters(self):
        '''
        This method updates the core properties of radius, position and angle to
        vertical for a new impact crater.
        '''
        #Need to ensure the elevs and grid have been updated before this call...
        if self.radius_auto_flag == 1:
            self.set_cr_radius_from_shoemaker()
        if self.position_auto_flag == 1:
            self.set_coords()
        if self.angle_auto_flag == 1:
            self.set_impactor_angles()
        
    def get_crater_shape_exp(self):
        '''
        This method assumes the max depth and radius of a crater are known.
        It provides n for a power law of form d = D*(r/R)**n, where D and R are
        the known values, by assuming the outer edges of the crater sit at angle
        of repose. This gives very sensible answers; n~2 for big, complex
        craters (Garvin et al, 2000, p.333: "There is a strong tendency for
        craters to become more paraboloidal with increasing diameter,
        independent of location.") and n~1.3 for ~2km simple craters (Garvin,
        following Croft has ~1.18).
        '''
        return 0.51 * self._radius / self._depth

    #Holsapple & Housen et al 1983 note that in the strength regime, ejecta
    #distribution will NOT scale independently of crater size (it does in the
    #gravity regime) - ejecta will be proportionally FURTHER FROM the rim as craters
    #get smaller (e.g., Housen et al 83, fig 9).
    #...this, of course, makes small craters disproportionately effective diffusers
    #- must find a way of quantifying this!
    #Moore et al 74 cited in Housen et al 83 gives Rce/R as const ~2.35 +0.56 -0.45
    #std err for Lunar craters 0.3-100km, where Rce is the continuous ejecta limit.
    #Below this, seems to level off abruptly - i.e., Rce ~ R**(<1) (and const will
    #change also to maintain connection at 0.3km).
    #A key point - geomorphic gardening is not totally comparable to regolith
    #gardening. We care about topology - the final crater. Regolith fracture and
    #turnover is more likely to depend on the crater transient depth.

    def set_cr_radius_from_shoemaker(self):
        '''
        This method takes a random number between 0 and 1, and returns a crater
        radius based on a py distn N = kD^-2.9, following Shoemaker et al., 1970.
        '''
        self._radius = self._minimum_crater*(random())**-0.345

    def set_coords(self):
        '''
        This method selects a random location inside the grid onto which to map
        an impact. It also sets variables for the closest grid node to the
        impact, and the elevation at that node.
        '''
        #NB - we should be allowing craters OUTSIDE the grid - as long as part of them impinges.
        #This would be relatively easy to implement - allow allocation out to the max crater we expect, then allow runs using these coords on our smaller grid. Can save comp time by checking if there will be impingement before doing the search.
        grid = self.grid
        self._xcoord = random() * (grid.get_grid_xdimension() - grid.dx)
        self._ycoord = random() * (grid.get_grid_ydimension() - grid.dx)
        #Snap impact to grid:
        self.closest_node_index = grid.snap_coords_to_grid(self._xcoord, self._ycoord)
        self.closest_node_elev = self.elev[self.closest_node_index]
        #NB - snapping to the grid may be quite computationally demanding in a Voronoi.

    def check_coords_and_angles_for_grazing(self):
        '''
        This method migrates the coords of a given impactor if its normal angle
        means it would clip other topo before striking home.
        It assumes both set_impactor_angles() and set_coords() have already both
        been called.
        '''
        sin_az = numpy.sin(self._azimuth_of_travel)
        cos_az = numpy.cos(self._azimuth_of_travel)
        alpha = self._azimuth_of_travel - numpy.pi
        cos_alpha = numpy.cos(alpha)
        sin_alpha = numpy.sin(alpha)
        grid = self.grid
        x = self._xcoord
        y = self._ycoord
        dx = grid.dx
        if sin_az>0.: #travelling E
            line_horiz = -x #...so line extends TO THE WEST
        else: #travelling W
            line_horiz = grid.get_grid_xdimension() - grid.dx - x
        if cos_az>0.: #travelling N
            line_vert = -y
        else: #travelling S
            line_vert = grid.get_grid_ydimension() - grid.dx - y
        #How many divisions?
        hyp_line_vert = line_vert/cos_alpha
        hyp_line_horiz = line_horiz/sin_alpha
        num_divisions = int(min(hyp_line_vert,hyp_line_horiz)//dx) #Spacing set to dx, ALONG LINE (so spacing always <dx in x,y)
        if num_divisions > 2:
            line_points = (numpy.arange(num_divisions-2)+1.)*dx #[dx,2dx,3dx...]; arbitrary reduction in length at end to keep clear of dodgy grid edge
            line_xcoords = x + sin_alpha*line_points
            line_ycoords = y + cos_alpha*line_points #negative dimensions should sort themselves out
            snapped_pts_along_line = grid.snap_coords_to_grid(line_xcoords,line_ycoords)
            try:
                impactor_elevs_along_line = line_points/numpy.tan(self._angle_to_vertical) + self.closest_node_elev
            except ZeroDivisionError: #vertical impactor
                pass
            else:
                try:
                    points_under_surface_reversed = impactor_elevs_along_line[::-1]<=self.elev[snapped_pts_along_line[::-1]]
                except IndexError: #only one item in array
                    assert len(impactor_elevs_along_line) == 1
                    points_under_surface_reversed = impactor_elevs_along_line<=self.elev[snapped_pts_along_line]
                if numpy.any(points_under_surface_reversed):
                    #reverse the array order as impactor comes from far and approaches the impact point
                    reversed_index = numpy.argmax(points_under_surface_reversed)
                    #If reversed_index is 0, the impact NEVER makes it above ground on the grid, and needs to be discarded:
                    if reversed_index:
                        index_of_impact = num_divisions - 4 - reversed_index
                        self._xcoord = line_xcoords[index_of_impact]
                        self._ycoord = line_ycoords[index_of_impact]
                        self.closest_node_index = snapped_pts_along_line[index_of_impact]
                        self.closest_node_elev = self.elev[self.closest_node_index]
                    else:
                        if self.position_auto_flag == 1:
                            self.draw_new_parameters()
                            self.check_coords_and_angles_for_grazing()
                        else:
                            #This is a duff crater; kill it by setting r=0
                            self._radius = 0.000001
                            print 'Aborted this crater. Its trajectory was not physically plausible!'
                            
        

    def set_impactor_angles(self):
        '''
        This method sets the angle of impact, assuming the only effect is
        rotation of the planet under the impactor bombardment (i.e., if the
        target looks like a circle to the oncoming impactor, there's more limb
        area there to hit). As long as target is rotating relative to the sun,
        other (directional) effects should cancel. i.e., it draws from a sine
        distribution.
        Angle is given to vertical.
        Also sets a random azimuth.
        '''
        self._angle_to_vertical =  abs(numpy.arcsin(random())) #gives sin distn with most values drawn nearer to 0
        self._azimuth_of_travel = random() * 2. * numpy.pi #equal chance of any azimuth
        #Shoemaker 1983 gives a speculative equn for the suppression of D by increasing impact angle (his equ 3), but effect is minor, and it's probably not worth the trouble.
        #Shoemaker 1962 (in book) apparently states low angle impacts are very rare.

    def set_depth_from_size(self):
        '''
        This method sets the maximum depth at the center for a crater of known
        (i.e., already set) radius.
        '''
        #Let's ignore the strength transition at the lowest size scales for now.
        self._depth = self._radius / self._simple_radius_depth_ratio_Pike

    def set_crater_volume(self):
        '''
        This method uses known crater depth and radius and sets the volume of
        the excavated cavity.
        Note this is is the cavity volume, not the subsurface excavation vol.
        The true excavated volume is used to set the ejecta volumes in the 
        ..._BAND_AID methods.
        '''
        radius = self._radius
        depth = self._depth
        self._cavity_volume = 0.51 * numpy.pi * depth * radius*radius*radius / (0.51*radius + 2.*depth)
        #A band-aid fix for AGU will be to set the volume as the *actual* excavated volume.
        
    def create_lambda_fn_for_ejecta_thickness(self):
        """
        This method takes the complicated equation that relates "flat" ejecta
        thickness (symmetrical, with impact angle=0) to radius and cavity volume
        which is set in __init__(), and solves it for a given pair of impact
        specific parameters, V_cavity & crater radius.
        Both the cavity volume and crater radius need to have been set before
        this method is called.
        Method returns a lambda function for the radially symmetrical ejecta
        thickness distribution as a function of distance from crater center, r.
        i.e., call unique_expression_for_local_thickness(r) to calculate a
        thickness.
        Added DEJH Sept 2013.
        """
        local_solution_for_rim_thickness = self.solution_for_rim_thickness[0].subs(self.V, self._cavity_volume)
        unique_expression_for_local_thickness = self.expression_for_local_thickness.subs({self.r0:self._radius, self.T:local_solution_for_rim_thickness})
        unique_expression_for_local_thickness = lambdify(self.r, unique_expression_for_local_thickness)
        return unique_expression_for_local_thickness

    def create_lambda_fn_for_ejecta_thickness_BAND_AID(self, excavated_vol):
        """
        This method takes the complicated equation that relates "flat" ejecta
        thickness (symmetrical, with impact angle=0) to radius and cavity volume
        which is set in __init__(), and solves it for a given pair of impact
        specific parameters, V_cavity & crater radius.
        Both the cavity volume and crater radius need to have been set before
        this method is called.
        Method returns a lambda function for the radially symmetrical ejecta
        thickness distribution as a function of distance from crater center, r.
        i.e., call unique_expression_for_local_thickness(r) to calculate a
        thickness.
        This method is exactly equivalent to the non _BAND_AID equivalent,
        except it takes the volume to use as a parameter rather than reading it
        from the impactor object.
        Added DEJH Sept 2013.
        """
        local_solution_for_rim_thickness = self.solution_for_rim_thickness[0].subs(self.V, excavated_vol)
        unique_expression_for_local_thickness = self.expression_for_local_thickness.subs({self.r0:self._radius, self.T:local_solution_for_rim_thickness})
        unique_expression_for_local_thickness = lambdify(self.r, unique_expression_for_local_thickness)
        return unique_expression_for_local_thickness


    def set_crater_mean_slope_v2(self):
        '''
        This method takes a crater of known radius, and which has already been
        "snapped" to the grid through snap_impact_to_grid(mygrid), and returns a
        spatially averaged value for the local slope of the preexisting topo
        beneath the cavity footprint. This version of the method works by taking
        four transects across the crater area every 45 degrees around its rim,
        calculating the slope along each, then setting the slope as the greatest,
        positive downwards and in the appropriate D8 direction. This function
        also sets the mean surface dip direction.
        In here, we start to assume a convex and structured grid, such that if
        pts N and W on the rim are in the grid, so is the point NW.
        This version is vectorized, and so hopefully faster.
        This method is largely superceded by set_crater_mean_slope_v3(), which
        uses an GIS-style routine to set slopes. However, it may still be 
        preferable for grid-marginal craters.
        DEJH, Sept 2013.
        '''
        grid = self.grid
        #It would be trivial to add more divisions, i.e., D16, D32, D64, etc. Code is setup for this; just add to the slope_pts lists, and adjust the set length of the arrays with divisions
        divisions = 4 #len(radial_points1), i.e., half the no. of points == the number of lines across the wheel
        half_crater_radius = 0.707 * self._radius
        #Work round clockwise from 12 o'clock
        slope_pts1 = numpy.array([[self._xcoord,self._ycoord+self._radius],
                     [self._xcoord+half_crater_radius,self._ycoord+half_crater_radius],
                     [self._xcoord+self._radius,self._ycoord],
                     [self._xcoord+half_crater_radius,self._ycoord-half_crater_radius]]) #(4,2)
        slope_pts2 = numpy.array([[self._xcoord,self._ycoord-self._radius],
                     [self._xcoord-half_crater_radius,self._ycoord-half_crater_radius],
                     [self._xcoord-self._radius,self._ycoord],
                     [self._xcoord-half_crater_radius,self._ycoord+half_crater_radius]]) #(4,2)
        inbounds_test1 = grid.is_point_on_grid(slope_pts1[:,0], slope_pts1[:,1])
        inbounds_test2 = grid.is_point_on_grid(slope_pts2[:,0], slope_pts2[:,1])
        distance_array = (inbounds_test1.astype(float) + inbounds_test2.astype(float))*self._radius
        radial_points1 = numpy.where(inbounds_test1, (slope_pts1[:,0],slope_pts1[:,1]), [[self._xcoord], [self._ycoord]])
        radial_points2 = numpy.where(inbounds_test2, (slope_pts2[:,0],slope_pts2[:,1]), [[self._xcoord], [self._ycoord]])
        radial_points1 = grid.snap_coords_to_grid(radial_points1[0,:], radial_points1[1,:])
        radial_points2 = grid.snap_coords_to_grid(radial_points2[0,:], radial_points2[1,:])
        #print 'radial pts arrays: ', radial_points1, radial_points2
        #print 'On grid?: ', inbounds_test1, inbounds_test2
        #print 'Dist array: ', distance_array
        slope_array = numpy.where(distance_array, (self.elev[radial_points1]-self.elev[radial_points2])/distance_array, numpy.nan)
        slope_array = numpy.arctan(slope_array)
        #if slope  is negative, it means the surface slopes broadly EAST
        try:    
            hi_mag_slope_index = numpy.nanargmax(numpy.fabs(slope_array))
            hi_mag_slope = slope_array[hi_mag_slope_index]
        except:
            self._surface_slope = 1.e-10
            self_direction = self._azimuth_of_travel
            print 'Unable to assign crater slope by this method. Is crater of size comparable with grid?'
            print 'Setting slope to zero'
        else:
            #print 'Slope array: ', slope_array
            if hi_mag_slope > 0.: #i.e., dips WEST (or dead S)
                self._surface_dip_direction = (hi_mag_slope_index/float(divisions) + 1.)*numpy.pi
            elif not hi_mag_slope: #i.e., FLAT, dip dir is arbitrary; set to the travel direction of the impactor
                self._surface_dip_direction = self._azimuth_of_travel
            else: #dips EAST
                self._surface_dip_direction = numpy.pi*hi_mag_slope_index/float(divisions)    
            self._surface_slope = numpy.fabs(hi_mag_slope)
            #print 'The slope under the crater cavity footprint is: ', self._surface_slope


    def set_crater_mean_slope_v3(self):
        '''
        Runs on a square which encapsulates the crater.
        If some of the nodes are off the grid, falls back on v2.
        This version uses the Horn, 1981 algorithm, the same one used by many
        GIS packages.
        '''
        grid = self.grid
        elev = self.elev
        r = self._radius
        x = self._xcoord
        y = self._ycoord
        dx = grid.dx
        slope_pts =numpy.array([[x-r,y-r],[x,y-r],[x+r,y-r],[x-r,y],[x,y],[x+r,y],[x-r,y+r],[x,y+r],[x+r,y+r]])
        pts_on_grid = grid.is_point_on_grid(slope_pts[:,0],slope_pts[:,1])
        if not numpy.all(pts_on_grid):
            slope_coords_ongrid = slope_pts[pts_on_grid]
            slope_pts_ongrid = grid.snap_coords_to_grid(slope_coords_ongrid[:,0],slope_coords_ongrid[:,1])
            cardinal_elevs = elev[slope_pts_ongrid]
            self.closest_node_index = grid.snap_coords_to_grid(self._xcoord, self._ycoord)
            self.set_crater_mean_slope_v2()
            ##new specialized method instead:
            #if not pts_on_grid[0]:
            #    active_pts = slope_pts[[4,5,7,8],:]
            #elif not pts_on_grid[8]:
            #    active_pts = slope_pts[[0,1,3,4],:]
            #elif not pts_on_grid[2]:
            #    active_pts = slope_pts[[3,4,6,7],:]
            #elif not pts_on_grid[6]:
            #    active_pts = slope_pts[[1,2,4,5],:]
            #slope_pts_ongrid = grid.snap_coords_to_grid(active_pts[:,0],active_pts[:,1])
            #elevs_of_pts = elev[slope_pts_ongrid]
            #hoz = (elevs_of_pts[0]+elevs_of_pts[2]-elevs_of_pts[1]-elevs_of_pts[3])/(2.*r)
            #vert = (elevs_of_pts[0]+elevs_of_pts[1]-elevs_of_pts[2]-elevs_of_pts[3])/(2.*r)
            #self._surface_slope = numpy.sqrt(hoz*hoz + vert*vert)
            #if not hoz:
            #    if vert<0.:
            #        self._surface_dip_direction = numpy.pi
            #    else:
            #        self._surface_dip_direction = 0.
            #else: #general case
            #    angle_to_xaxis = numpy.arctan(vert/hoz) #+ve is CCW rotation from x axis
            #    self._surface_dip_direction = ((1.-numpy.sign(hoz))*0.5)*numpy.pi + (0.5*numpy.pi-angle_to_xaxis)
        else:
            slope_pts_ongrid = grid.snap_coords_to_grid(slope_pts[:,0],slope_pts[:,1])
            self.closest_node_index = slope_pts_ongrid[4]
            cardinal_elevs = elev[slope_pts_ongrid]
            #Now the Horn '81 algorithm for weighted max slope: (careful w signs! altered to give down as +ve)
            S_we = ((cardinal_elevs[6]+2*cardinal_elevs[3]+cardinal_elevs[0])-(cardinal_elevs[8]+2*cardinal_elevs[5]+cardinal_elevs[2]))/(8.*r)
            S_sn = ((cardinal_elevs[0]+2*cardinal_elevs[1]+cardinal_elevs[2])-(cardinal_elevs[6]+2*cardinal_elevs[7]+cardinal_elevs[8]))/(8.*r)
            self._surface_slope = numpy.sqrt(S_we*S_we + S_sn*S_sn)
            if not S_we:
                if S_sn<0.:
                    self._surface_dip_direction = numpy.pi
                else:
                    self._surface_dip_direction = 0.
            else: #general case
                angle_to_xaxis = numpy.arctan(S_sn/S_we) #+ve is CCW rotation from x axis
                self._surface_dip_direction = ((1.-numpy.sign(S_we))*0.5)*numpy.pi + (0.5*numpy.pi-angle_to_xaxis)
        self.closest_node_elev = numpy.mean(cardinal_elevs)

    #@profile
    def set_elev_change_only_beneath_footprint(self):
        '''
        NB- this method is currently superceded by the ...BAND_AID() equivalent,
        below. That method handles the mass balance in the impact in a superior
        way.
        This is a method to take an existing impact properties and a known 
        nearest node to the impact site, and alter the topography to model the 
        impact. It assumes crater radius and depth are known, models cavity 
        shape as a power law where n is a function of R/D, and models ejecta 
        thickness as an exponential decay,sensitive to both ballistic range from 
        tilting and momentum transfer in impact (after Furbish). We DO NOT yet 
        model transition to peak ring craters, or enhanced diffusion by ejecta 
        in the strength regime. All craters are dug perpendicular to the geoid, 
        not the surface.
        This version of the code does NOT correct for slope dip direction - 
        because Furbish showed momentum almost always wins, and these impactors 
        have a lot of momentum!
        NB - this function ASSUMES that the "beta factor" in the model is <=0.5, 
        i.e., nonlinearities can't develop in the ejecta field, and the impact 
        point is always within the (circular) ejecta footprint.
        Created DEJH Sept 2013.
        '''     
        #Load in the data, for speed and conciseness:
        _angle_to_vertical=self._angle_to_vertical
        _surface_slope=self._surface_slope
        _surface_dip_direction = self._surface_dip_direction
        _azimuth_of_travel = self._azimuth_of_travel
        pi = numpy.pi
        twopi = 2.*pi
        tan = numpy.tan
        cos = numpy.cos
        sin = numpy.sin
        sqrt = numpy.sqrt
        #arctan = numpy.arctan
        arccos = numpy.arccos
        where = numpy.where
        _radius = self._radius
        elev = self.elev
        tan_repose = self.tan_repose
        grid = self.grid
        
        #Derive the exponent for the crater shape, shared betw simple & complex:
        crater_bowl_exp = self.get_crater_shape_exp()
        
        #Derive the effective angle for impact, relative to the surface normal, beta_eff. The direction is always controlled by the impactor. Draw new impact angles if impact geomtery is impossible.
        #Ejecta always fired along line of impact, but it is sensitive to surface slope
        while 1:
            #epsilon is the angle between the surface normal and the impactor angle to vertical, projected along the line of travel.
            rake_in_surface_plane = _surface_dip_direction - _azimuth_of_travel
            #print '_surface_dip_direction: ', _surface_dip_direction
            #print '_azimuth_of_travel: ', _azimuth_of_travel
            #print '_angle_to_vertical: ', _angle_to_vertical
            absolute_rake = numpy.fabs(rake_in_surface_plane)
            #We need these to keep the solution for epsilon, below, stable (it has asymptotes on pi/2)
            if not _surface_slope: #surface is totally flat
                epsilon = 0.
                beta_eff = _angle_to_vertical
            else:
                if absolute_rake in (0.5*pi, 1.5*pi): #impact along line of strike
                    epsilon = 0.
                else:
                    epsilon = arccos(cos(rake_in_surface_plane)*cos(_surface_slope)*sqrt(1.+(tan(rake_in_surface_plane)/cos(_surface_slope))**2.))
                #Make the necessary adjustment to the angle to vertical to reflect dipping surface:
                if absolute_rake <= 0.5*pi or absolute_rake >= 1.5*pi:
                    beta_eff = _angle_to_vertical + epsilon
                else:
                    beta_eff = _angle_to_vertical + epsilon - pi
            #print 'Beta effective: ', beta_eff
            #Make correction to ejecta direction needed if angle to normal is small and slope is large in the opposite direction:
            if 0. <= beta_eff <= 90.:
                _ejecta_azimuth = _azimuth_of_travel
                break
            elif beta_eff < 0.:
                #reverse the azimuth, make beta positive again
                beta_eff = -beta_eff
                _ejecta_azimuth = (_azimuth_of_travel+pi)%twopi
                break
            else:
                print 'Impact geometry was not possible! Refreshing the impactor angle...'
                self.set_impactor_angles()
                _azimuth_of_travel = self._azimuth_of_travel
                _angle_to_vertical = self._angle_to_vertical

        #apply correction to beta to suppress nonlinear BC problem and make ejecta patterns "look like" they actually do:
        tan_beta = tan(beta_eff*self._beta_factor)
        tan_beta_sqd = tan_beta*tan_beta
        #print 'Impact, ejecta azimuths: ', _azimuth_of_travel, _ejecta_azimuth

        unique_expression_for_local_thickness = self.create_lambda_fn_for_ejecta_thickness()
        thickness_at_rim = unique_expression_for_local_thickness(_radius)
        
        #The center is transposed by rmax*tan(beta) in the direction of impact:
        #Might be an issue here - we use the rmax, so doesn't the *position* of the max thickness then also depend on rmax??
        #Should it actually just be r? -> no, use the MEAN: see Furbish para 27? For now, just use the max radius - this will be conservative, at least.
        #solve the ejecta radial thickness equ w. the min thickness to get the max radius:
        max_radius_ejecta_on_flat = _radius * (thickness_at_rim/self._minimum_ejecta_thickness)**0.3636 #+ 0.5*grid.dx #0.5dx added as a conservative buffer, as we are going to use the closest node as the center... Now removed as we don't use this method
        #print 'thickness_at_rim: ', thickness_at_rim
        #print 'max_radius_ejecta: ', max_radius_ejecta_on_flat
        
        displacement_distance = max_radius_ejecta_on_flat*tan_beta
        #need a variable for if the exacavated radius is ever outside the locus defined by the MREOF around the footprint center:
        excess_excavation_radius = (max_radius_ejecta_on_flat - displacement_distance) < _radius
        
        footprint_center_x = self._xcoord+sin(_azimuth_of_travel)*displacement_distance
        footprint_center_y = self._ycoord+cos(_azimuth_of_travel)*displacement_distance
        
        #This material assumed we could construct a distance map for all nodes, but it's too big.
        #Instead, we're going to simplify the footprint to a rectangle, not circle, to accelerate the search.
        # => added self.create_square_footprint() below; used it beneath this comment block.
        #if 0. < footprint_center_x < (grid.get_grid_xdimension()-grid.dx) and 0. < footprint_center_y < (grid.get_grid_ydimension()-grid.dx):
        #    closest_node_to_footprint_center = grid.snap_coords_to_grid(footprint_center_x, footprint_center_y)
        #    footprint_nodes = self.all_node_distances_map[closest_node_to_footprint_center,:] < max_radius_ejecta_on_flat
        #else:
        #    footprint_nodes = grid.get_distances_of_nodes_to_point((footprint_center_x, footprint_center_y)) < max_radius_ejecta_on_flat
        #if excess_excavation_radius:
        #    dist_to_center = self.all_node_distances_map[self.closest_node_index,:]        
        #    footprint_nodes = numpy.logical_or(dist_to_center<_radius, footprint_nodes)

        footprint_nodes = self.create_square_footprint((footprint_center_x,footprint_center_y),max_radius_ejecta_on_flat)
        if excess_excavation_radius:
            print 'A low-angle crater!'
            cavity_footprint = self.create_square_footprint((self._xcoord,self._ycoord),_radius)
            footprint_nodes = numpy.unique(numpy.concatenate((footprint_nodes,cavity_footprint))) #combine the two footprints into array of unique IDs
                
        _vec_r_to_center, _vec_theta = grid.get_distances_of_nodes_to_point((self._xcoord,self._ycoord), get_az='angles', node_subset=footprint_nodes)
        #Disabled, 112713
        #slope_offsets_rel_to_center = _vec_r_to_center*numpy.tan(self._surface_slope)*numpy.cos(_vec_theta-self._surface_dip_direction)
        
        #Define a shortcut identity for the elev[footprint_nodes] patch, so we don't have to keep looking it up:
        elevs_under_footprint = elev[footprint_nodes]
        ##make a local copy of the elevations under the footprint, so we can do a mass balance at the end:
        #old_elevs_under_footprint = numpy.copy(elevs_under_footprint)
        #NO! This deep copying creates runaway memory allocation through loops. Better is:
        old_elevs_under_footprint = elevs_under_footprint.copy()
        
        ##We need to account for deposition depth elevating the crater rim, i.e., we need to deposit *before* we cut the cavity. We do this by defining three domains for the node to lie in: 1. r<r_calc, i.e., below the pre-impact surface. No risk of intersecting the surface here. 2. r_calc < r; Th>z_new. this is the domain in the inward sloping rim of the crater ejecta. 3. Th<z_new and beyond. out on the ejecta proper. Note - (1) is not hard & fast rule if the surface dips. Safer is just (Th-lowering)<z_new
        ##So, calc the excavation depth for all nodes, just to be on the safe side for strongly tilted geometries:
        #(_vec_new_z is the depth that would be excavated inside the cavity, including projected depths ouside the cavity.
        _vec_new_z = numpy.empty_like(_vec_r_to_center)
        _nodes_within_crater = _vec_r_to_center<=_radius
        _nodes_outside_crater = numpy.logical_not(_nodes_within_crater)
        _vec_new_z.fill(self.closest_node_elev + thickness_at_rim - self._depth)
        #Disabled, 112713
        #_vec_new_z[_nodes_within_crater] += self._depth * (_vec_r_to_center[_nodes_within_crater]/_radius)**crater_bowl_exp - slope_offsets_rel_to_center[_nodes_within_crater]
        #_vec_new_z[_nodes_outside_crater] += self._depth + (_vec_r_to_center[_nodes_outside_crater]-_radius)*tan_repose - slope_offsets_rel_to_center[_nodes_outside_crater]
        _vec_new_z[_nodes_within_crater] += self._depth * (_vec_r_to_center[_nodes_within_crater]/_radius)**crater_bowl_exp
        _vec_new_z[_nodes_outside_crater] += self._depth + (_vec_r_to_center[_nodes_outside_crater]-_radius)*tan_repose
        _nodes_below_surface = _vec_new_z<elevs_under_footprint
        _nodes_above_surface = numpy.logical_not(_nodes_below_surface)
        #Set the ground elev for below ground nodes
        elevs_under_footprint[_nodes_below_surface] = _vec_new_z[_nodes_below_surface]
        #From here on, our new arrays will only be as long as nodes_above_surface
        _vec_flat_thickness_above_surface = unique_expression_for_local_thickness(_vec_r_to_center[_nodes_above_surface])
        _vec_theta_eff = _ejecta_azimuth - _vec_theta[_nodes_above_surface] #This is the angle of the center-to-active-node line to the azimuth along which the ejecta is concentrated
        _vec_sin_theta_sqd = sin(_vec_theta_eff) ** 2.
        _vec_cos_theta = cos(_vec_theta_eff)
        
        #This material is not necessary as part of this footprint-based method, as we already forbid beta_factor>0.5
        ##REMEMBER, as tan_beta gets >1, the function describing the ejecta is only valid over ever more restricted ranges of theta!! In other words,
        #nodes_inside_ejecta = where(_vec_sin_theta_sqd*tan_beta_sqd <= 1.) #these are indices to an array of length nodes_above_surface only
        #_vec_thickness = numpy.zeros(nodes_above_surface.size) #i.e., it's zero outside the ejecta
        _vec_mu_theta_by_mu0 = tan_beta * _vec_cos_theta + sqrt(1. - _vec_sin_theta_sqd * tan_beta_sqd)
        _vec_f_theta = (tan_beta_sqd*(_vec_cos_theta**2.-_vec_sin_theta_sqd) + 2.*tan_beta*_vec_cos_theta*sqrt(1.-tan_beta_sqd*_vec_sin_theta_sqd) + 1.) / twopi
        #So, distn_at_angle = distn_vertical_impact*f_theta/mu_theta_by_mu0. Draw the thickness at the active node:
        #NB-the 2pi is to correct for mismatch in the dimensions of mu and f
        _vec_thickness = _vec_f_theta/_vec_mu_theta_by_mu0 * twopi * _vec_flat_thickness_above_surface
        #Set the thicknesses <0 to 0:
        _vec_thickness_positive = where(_vec_thickness>=0.,_vec_thickness, 0.)
        #Now, are we inside or outside the rim?
        elevs_under_footprint[_nodes_above_surface] = where(_vec_new_z[_nodes_above_surface]<=(elevs_under_footprint[_nodes_above_surface]+_vec_thickness_positive),_vec_new_z[_nodes_above_surface], elevs_under_footprint[_nodes_above_surface]+_vec_thickness_positive)

        #Save any data to the higher level:      
        self.elev[footprint_nodes] = elevs_under_footprint
        elev_diff = elevs_under_footprint-old_elevs_under_footprint
        self.mass_balance_in_impact = numpy.sum(elev_diff)/-numpy.sum(elev_diff[elev_diff<0.]) #positive is mass gain, negative is mass loss. This is currently a mass fraction, given relative to volume (h*px#) excavated from below the original surface.
        self.ejecta_azimuth = _ejecta_azimuth
        self.impactor_angle_to_surface_normal = beta_eff #note in this case this is the *effective* angle (in the direction of travel), not the actual angle to the surface.
        #print 'Vol of crater cavity: ', self._cavity_volume
        
        #Uncomment this line to see which nodes are under the footprint:
        #self.elev[footprint_nodes] = 10.
        #self.elev[footprint_nodes[_nodes_within_crater]] = 20.
        
        del(_vec_r_to_center)
        del(_vec_theta)
        del(_vec_new_z)
        del(_nodes_below_surface)
        del(_nodes_above_surface)
        del(_vec_sin_theta_sqd)
        del(_vec_mu_theta_by_mu0)
        del(_vec_f_theta)
        del(_vec_thickness_positive)
        del(elevs_under_footprint)
        del(elev_diff)
        
        return self.elev
        
    def set_elev_change_only_beneath_footprint_BAND_AID(self):
        '''
        This is a method to take an existing impact properties and a known 
        nearest node to the impact site, and alter the topography to model the 
        impact. It assumes crater radius and depth are known, models cavity 
        shape as a power law where n is a function of R/D, and models ejecta 
        thickness as an exponential decay,sensitive to both ballistic range from 
        tilting and momentum transfer in impact (after Furbish). We DO NOT yet 
        model transition to peak ring craters, or enhanced diffusion by ejecta 
        in the strength regime. All craters are dug perpendicular to the geoid, 
        not the surface.
        This version of the code does NOT correct for slope dip direction - 
        because Furbish showed momentum almost always wins, and these impactors 
        have a lot of momentum!
        NB - this function ASSUMES that the "beta factor" in the model is <=0.5, 
        i.e., nonlinearities can't develop in the ejecta field, and the impact 
        point is always within the (circular) ejecta footprint.
        This version of this method ("_band_aid"!) uses a quick and dirty fix 
        which substitutes the actual excavated volume into the equn to derive 
        ejecta thicknesses. It also digs craters perpendicular to the local 
        surface, mimicking some aspects of a "strength dominated" impact - 
        unless doing so would create extremely strongly tilted craters, excavate
        large sheets of material, or otherwise destabilize the mass balance of
        the component.
        It pays no regard for the inefficiency of doing that!
        Created DEJH Dec 2013
        '''     
        #Load in the data, for speed and conciseness:
        _angle_to_vertical=self._angle_to_vertical
        _surface_slope=self._surface_slope
        _surface_dip_direction = self._surface_dip_direction
        _azimuth_of_travel = self._azimuth_of_travel
        pi = numpy.pi
        twopi = 2.*pi
        tan = numpy.tan
        cos = numpy.cos
        sin = numpy.sin
        sqrt = numpy.sqrt
        #arctan = numpy.arctan
        arccos = numpy.arccos
        where = numpy.where
        _radius = self._radius
        elev = self.elev
        tan_repose = self.tan_repose
        grid = self.grid
        self.cheater_flag = 0
        
        #Derive the exponent for the crater shape, shared betw simple & complex:
        crater_bowl_exp = self.get_crater_shape_exp()
        
        #Derive the effective angle for impact, relative to the surface normal, beta_eff. The direction is always controlled by the impactor. Draw new impact angles if impact geomtery is impossible.
        while 1:
            #epsilon is the angle between the surface normal and the impactor angle to vertical, projected along the line of travel.
            rake_in_surface_plane = _surface_dip_direction - _azimuth_of_travel
            #print '_surface_dip_direction: ', _surface_dip_direction
            #print '_azimuth_of_travel: ', _azimuth_of_travel
            #print '_angle_to_vertical: ', _angle_to_vertical
            absolute_rake = numpy.fabs(rake_in_surface_plane)
            if not _surface_slope:
                epsilon = 0.
                beta_eff = _angle_to_vertical
            else:
                if absolute_rake in (0.5*pi, 1.5*pi):
                    epsilon = 0.
                else:
                    epsilon = arccos(cos(rake_in_surface_plane)*cos(_surface_slope)*sqrt(1.+(tan(rake_in_surface_plane)/cos(_surface_slope))**2.))
                #Make the necessary adjustment to the angle to vertical to reflect dipping surface:
                if absolute_rake <= 0.5*pi or absolute_rake >= 1.5*pi:
                    beta_eff = _angle_to_vertical + epsilon
                else:
                    beta_eff = _angle_to_vertical + epsilon - pi
            #print 'Beta effective: ', beta_eff
            #Make correction to ejecta direction needed if angle to normal is small and slope is large in the opposite direction:
            if 0. <= beta_eff <= 90.:
                _ejecta_azimuth = _azimuth_of_travel
                break
            elif beta_eff < 0.:
                #reverse the azimuth, make beta positive again
                beta_eff = -beta_eff
                _ejecta_azimuth = (_azimuth_of_travel+pi)%twopi
                break
            else:
                #Note this situation should now be forbidden as we correct for grazing impacts
                print 'Impact geometry was not possible! Refreshing the impactor angle...'
                self.set_impactor_angles()
                _azimuth_of_travel = self._azimuth_of_travel
                _angle_to_vertical = self._angle_to_vertical

        #apply correction to beta to suppress nonlinear BC problem and make ejecta patterns "look like" they actually do:
        tan_beta = tan(beta_eff*self._beta_factor)
        tan_beta_sqd = tan_beta*tan_beta
        #print 'Impact, ejecta azimuths: ', _azimuth_of_travel, _ejecta_azimuth
        
        ###Here's the oh-so-ugly ad-hoc fix to address the mass balance problems
        #We're calculating the approx excavated volume directly to feed the ejecta thickness calculator...
        excavation_nodes = self.create_square_footprint((self._xcoord,self._ycoord),4.*_radius) #Arbitrary increase in radius to try to catch the additional excavated material
        #sharp lips w/i the grid are CATASTROPHIC (=>get super-steep slopes) - this 4* really has to be sufficient.
        _vec_r_to_center_excav,_vec_theta_excav = grid.get_distances_of_nodes_to_point((self._xcoord,self._ycoord), get_az='angles', node_subset=excavation_nodes)
        slope_offsets_rel_to_center_excav = -_vec_r_to_center_excav*numpy.tan(self._surface_slope)*numpy.cos(_vec_theta_excav-self._surface_dip_direction) #node leading negative - downslopes are +ve!!
        _vec_new_z_excav = slope_offsets_rel_to_center_excav + self.closest_node_elev - 1.*self._depth #Arbitrary assumed scaling: rim is 0.1 of total depth
        _vec_new_z_excav += self._depth * (_vec_r_to_center_excav/_radius)**crater_bowl_exp #note there's another fix here - we've relaxed the constraint that slopes outside one radius are at repose. It will keep curling up, so poke out quicker
        z_difference = self.elev[excavation_nodes] - _vec_new_z_excav #+ve when excavating
        excavated_volume = numpy.sum(numpy.where(z_difference>0.,z_difference,0.))*grid.dx*grid.dx
        #This all assumes we're representing the surface slope accurately now. If we are, then the real radius will remain roughly as the calculated value as we shear the crater into the surface. If not, could have problems.
        
        unique_expression_for_local_thickness = self.create_lambda_fn_for_ejecta_thickness_BAND_AID(excavated_volume)
        thickness_at_rim = unique_expression_for_local_thickness(_radius)
        if thickness_at_rim<0.:
            thickness_at_rim = 0.
        #print 'Surface_slope: ', _surface_slope
        #print 'Depth: ', self._depth
        #print 'Rim thickness: ', thickness_at_rim
        #####
        
        #The center is transposed by rmax*tan(beta) in the direction of impact:
        #Might be an issue here - we use the rmax, so doesn't the *position* of the max thickness then also depend on rmax??
        #Should it actually just be r? -> no, use the MEAN: see Furbish para 27? For now, just use the max radius - this will be conservative, at least.
        #solve the ejecta radial thickness equ w. the min thickness to get the max radius:
        max_radius_ejecta_on_flat = _radius * (thickness_at_rim/self._minimum_ejecta_thickness)**0.3636 #+ 0.5*grid.dx #0.5dx added as a conservative buffer, as we are going to use the closest node as the center... Now removed as we don't use this method
        #print 'thickness_at_rim: ', thickness_at_rim
        #print 'max_radius_ejecta: ', max_radius_ejecta_on_flat
        
        displacement_distance = max_radius_ejecta_on_flat*tan_beta
        #need a variable for if the exacavated radius is ever outside the locus defined by the MREOF around the footprint center:
        excess_excavation_radius = (max_radius_ejecta_on_flat - displacement_distance) < 4.*_radius
        
        footprint_center_x = self._xcoord+sin(_azimuth_of_travel)*displacement_distance
        footprint_center_y = self._ycoord+cos(_azimuth_of_travel)*displacement_distance
        
        #This material assumed we could construct a distance map for all nodes, but it's too big.
        #Instead, we're going to simplify the footprint to a rectangle, not circle, to accelerate the search.
        # => added self.create_square_footprint() below; used it beneath this comment block.
        #if 0. < footprint_center_x < (grid.get_grid_xdimension()-grid.dx) and 0. < footprint_center_y < (grid.get_grid_ydimension()-grid.dx):
        #    closest_node_to_footprint_center = grid.snap_coords_to_grid(footprint_center_x, footprint_center_y)
        #    footprint_nodes = self.all_node_distances_map[closest_node_to_footprint_center,:] < max_radius_ejecta_on_flat
        #else:
        #    footprint_nodes = grid.get_distances_of_nodes_to_point((footprint_center_x, footprint_center_y)) < max_radius_ejecta_on_flat
        #if excess_excavation_radius:
        #    dist_to_center = self.all_node_distances_map[self.closest_node_index,:]        
        #    footprint_nodes = numpy.logical_or(dist_to_center<_radius, footprint_nodes)

        footprint_nodes = self.create_square_footprint((footprint_center_x,footprint_center_y),max_radius_ejecta_on_flat)
        if excess_excavation_radius:
            print 'A low-angle crater!'
            cavity_footprint = self.create_square_footprint((self._xcoord,self._ycoord),5.*_radius)
            footprint_nodes = numpy.unique(numpy.concatenate((footprint_nodes,cavity_footprint))) #combine the two footprints into array of unique IDs
        
        _vec_r_to_center, _vec_theta = grid.get_distances_of_nodes_to_point((self._xcoord,self._ycoord), get_az='angles', node_subset=footprint_nodes)
        
        #Define a shortcut identity for the elev[footprint_nodes] patch, so we don't have to keep looking it up:
        elevs_under_footprint = elev[footprint_nodes]
        ##make a local copy of the elevations under the footprint, so we can do a mass balance at the end:
        #old_elevs_under_footprint = numpy.copy(elevs_under_footprint)
        #NO! This deep copying creates runaway memory allocation through loops. Better is:
        old_elevs_under_footprint = elevs_under_footprint.copy()
        
        ##We need to account for deposition depth elevating the crater rim, i.e., we need to deposit *before* we cut the cavity. We do this by defining three domains for the node to lie in: 1. r<r_calc, i.e., below the pre-impact surface. No risk of intersecting the surface here. 2. r_calc < r; Th>z_new. this is the domain in the inward sloping rim of the crater ejecta. 3. Th<z_new and beyond. out on the ejecta proper. Note - (1) is not hard & fast rule if the surface dips. Safer is just (Th-lowering)<z_new
        ##So, calc the excavation depth for all nodes, just to be on the safe side for strongly tilted geometries:
        #(_vec_new_z is the depth that would be excavated inside the cavity, including projected depths ouside the cavity.
        _nodes_within_crater = _vec_r_to_center<=_radius
        _nodes_outside_crater = numpy.logical_not(_nodes_within_crater)
        _vec_new_z = -_vec_r_to_center*numpy.tan(self._surface_slope)*numpy.cos(_vec_theta-self._surface_dip_direction) + self.closest_node_elev + thickness_at_rim - self._depth
        _vec_new_z[_nodes_within_crater] += self._depth * (_vec_r_to_center[_nodes_within_crater]/_radius)**crater_bowl_exp
        _vec_new_z[_nodes_outside_crater] += self._depth + (_vec_r_to_center[_nodes_outside_crater]-_radius)*tan_repose
        _nodes_below_surface = _vec_new_z<elevs_under_footprint
        _nodes_above_surface = numpy.logical_not(_nodes_below_surface)
        _vec_theta_eff = _ejecta_azimuth - _vec_theta[_nodes_above_surface] #This is the angle of the center-to-active-node line to the azimuth along which the ejecta is concentrated

        #Set the ground elev for below ground nodes
        elevs_under_footprint[_nodes_below_surface] = _vec_new_z[_nodes_below_surface]
        #From here on, our new arrays will only be as long as nodes_above_surface
        _vec_flat_thickness_above_surface = unique_expression_for_local_thickness(_vec_r_to_center[_nodes_above_surface])
        _vec_sin_theta_sqd = sin(_vec_theta_eff) ** 2.
        _vec_cos_theta = cos(_vec_theta_eff)
        
        #This material is not necessary as part of this footprint-based method, as we already forbid beta_factor>0.5
        ##REMEMBER, as tan_beta gets >1, the function describing the ejecta is only valid over ever more restricted ranges of theta!! In other words,
        #nodes_inside_ejecta = where(_vec_sin_theta_sqd*tan_beta_sqd <= 1.) #these are indices to an array of length nodes_above_surface only
        #_vec_thickness = numpy.zeros(nodes_above_surface.size) #i.e., it's zero outside the ejecta
        _vec_mu_theta_by_mu0 = tan_beta * _vec_cos_theta + sqrt(1. - _vec_sin_theta_sqd * tan_beta_sqd)
        _vec_f_theta = (tan_beta_sqd*(_vec_cos_theta**2.-_vec_sin_theta_sqd) + 2.*tan_beta*_vec_cos_theta*sqrt(1.-tan_beta_sqd*_vec_sin_theta_sqd) + 1.) / twopi
        #So, distn_at_angle = distn_vertical_impact*f_theta/mu_theta_by_mu0. Draw the thickness at the active node:
        #NB-the 2pi is to correct for mismatch in the dimensions of mu and f
        _vec_thickness = _vec_f_theta/_vec_mu_theta_by_mu0 * twopi * _vec_flat_thickness_above_surface
        #print self._xcoord, self._ycoord
        #print 'angle to vertical', self._angle_to_vertical
        #print 'effective beta, impactor dir of travel: ', beta_eff, self._azimuth_of_travel
        #if len(_vec_thickness) == 0:
        #    print self._radius
        #    print self._surface_dip_direction
        #    print self._surface_slope
        #    print len(footprint_nodes)
        #    print len(excavation_nodes)
        #    input()
        #Set the thicknesses <0 to 0:
        _vec_thickness_positive = where(_vec_thickness>=0.,_vec_thickness, 0.)
        #Now, are we inside or outside the rim?
        elevs_under_footprint[_nodes_above_surface] = where(_vec_new_z[_nodes_above_surface]<=(elevs_under_footprint[_nodes_above_surface]+_vec_thickness_positive),_vec_new_z[_nodes_above_surface], elevs_under_footprint[_nodes_above_surface]+_vec_thickness_positive)
        elev_diff = elevs_under_footprint-old_elevs_under_footprint
        init_mass_balance = numpy.sum(elev_diff)/-numpy.sum(elev_diff[elev_diff<0.])
        #add this method to try to kill the instability resulting from ever-increasing slopes:
        if init_mass_balance < -0.9:
            print 'ignoring slope effect for this impact...'
            self.cheater_flag = 1
            #footprint_nodes = numpy.arange(grid.number_of_nodes,dtype=int) #all the nodes, this time
            #previous setting of footprint_nodes will be adequate...
            _vec_r_to_center, _vec_theta = grid.get_distances_of_nodes_to_point((self._xcoord,self._ycoord), get_az='angles', node_subset=footprint_nodes)
            elevs_under_footprint = elev[footprint_nodes]
            old_elevs_under_footprint = elevs_under_footprint.copy()
            _nodes_within_crater = _vec_r_to_center<=_radius
            _nodes_outside_crater = numpy.logical_not(_nodes_within_crater)
            _vec_new_z = numpy.empty_like(_vec_theta)
            #Now, we DON'T incorporate the surface tilting. Trying to reset local slopes to repose.
            _vec_new_z.fill(self.closest_node_elev + thickness_at_rim - self._depth)
            _vec_new_z[_nodes_within_crater] += self._depth * (_vec_r_to_center[_nodes_within_crater]/_radius)**crater_bowl_exp
            _vec_new_z[_nodes_outside_crater] += self._depth + (_vec_r_to_center[_nodes_outside_crater]-_radius)*tan_repose
            _nodes_below_surface = _vec_new_z<elevs_under_footprint
            _nodes_above_surface = numpy.logical_not(_nodes_below_surface)
            _vec_theta_eff = _ejecta_azimuth - _vec_theta[_nodes_above_surface] #This is the angle of the center-to-active-node line to the azimuth along which the ejecta is concentrated
            elevs_under_footprint[_nodes_below_surface] = _vec_new_z[_nodes_below_surface]
            _vec_flat_thickness_above_surface = unique_expression_for_local_thickness(_vec_r_to_center[_nodes_above_surface])
            _vec_sin_theta_sqd = sin(_vec_theta_eff) ** 2.
            _vec_cos_theta = cos(_vec_theta_eff)
            _vec_mu_theta_by_mu0 = tan_beta * _vec_cos_theta + sqrt(1. - _vec_sin_theta_sqd * tan_beta_sqd)
            _vec_f_theta = (tan_beta_sqd*(_vec_cos_theta**2.-_vec_sin_theta_sqd) + 2.*tan_beta*_vec_cos_theta*sqrt(1.-tan_beta_sqd*_vec_sin_theta_sqd) + 1.) / twopi
            _vec_thickness = _vec_f_theta/_vec_mu_theta_by_mu0 * twopi * _vec_flat_thickness_above_surface
            _vec_thickness_positive = where(_vec_thickness>=0.,_vec_thickness, 0.)
            elevs_under_footprint[_nodes_above_surface] = where(_vec_new_z[_nodes_above_surface]<=(elevs_under_footprint[_nodes_above_surface]+_vec_thickness_positive),_vec_new_z[_nodes_above_surface], elevs_under_footprint[_nodes_above_surface]+_vec_thickness_positive)
            elev_diff = elevs_under_footprint-old_elevs_under_footprint
            init_mass_balance = numpy.sum(elev_diff)/-numpy.sum(elev_diff[elev_diff<0.])

        #Save any data to the higher level:      
        self.elev[footprint_nodes] = elevs_under_footprint
        elev_diff = elevs_under_footprint-old_elevs_under_footprint
        self.mass_balance_in_impact = numpy.sum(elev_diff)/-numpy.sum(elev_diff[elev_diff<0.]) #positive is mass gain, negative is mass loss. This is currently a mass fraction, given relative to volume (h*px#) excavated from below the original surface.
        self.ejecta_azimuth = _ejecta_azimuth
        self.impactor_angle_to_surface_normal = beta_eff #note in this case this is the *effective* angle (in the direction of travel), not the actual angle to the surface.
        #print 'Vol of crater cavity: ', self._cavity_volume
        
        #Uncomment this line to see which nodes are under the footprint:
        #data.elev[footprint_nodes] = 10.
        
        return self.elev

    def set_elev_change_only_beneath_footprint_no_angular(self):
        '''
        This method is in all respects equivalent to the above similarly named 
        code, but it does not use either impact or surface angles in drawing the
        impact ejecta. The impact ejecta field is always radially symmetric 
        about the impact point.
        '''     
        #Load in the data, for speed and conciseness:
        _angle_to_vertical=self._angle_to_vertical
        _surface_slope=self._surface_slope
        _surface_dip_direction = self._surface_dip_direction
        _azimuth_of_travel = self._azimuth_of_travel
        pi = numpy.pi
        twopi = 2.*pi
        tan = numpy.tan
        cos = numpy.cos
        sin = numpy.sin
        sqrt = numpy.sqrt
        #arctan = numpy.arctan
        arccos = numpy.arccos
        where = numpy.where
        _radius = self._radius
        elev = self.elev
        tan_repose = self.tan_repose
        grid = self.grid
        
        #Derive the exponent for the crater shape, shared betw simple & complex:
        crater_bowl_exp = self.get_crater_shape_exp()
        
        #Derive the effective angle for impact, relative to the surface normal, beta_eff. The direction is always controlled by the impactor. Draw new impact angles if impact geomtery is impossible.
        while 1:
            #epsilon is the angle between the surface normal and the impactor angle to vertical, projected along the line of travel.
            rake_in_surface_plane = _surface_dip_direction - _azimuth_of_travel
            #print '_surface_dip_direction: ', _surface_dip_direction
            #print '_azimuth_of_travel: ', _azimuth_of_travel
            #print '_angle_to_vertical: ', _angle_to_vertical
            absolute_rake = numpy.fabs(rake_in_surface_plane)
            if not _surface_slope:
                epsilon = 0.
                beta_eff = _angle_to_vertical
            else:
                if absolute_rake in (0.5*pi, 1.5*pi):
                    epsilon = 0.
                else:
                    epsilon = arccos(cos(rake_in_surface_plane)*cos(_surface_slope)*sqrt(1.+(tan(rake_in_surface_plane)/cos(_surface_slope))**2.))
                #Make the necessary adjustment to the angle to vertical to reflect dipping surface:
                if absolute_rake <= 0.5*pi or absolute_rake >= 1.5*pi:
                    beta_eff = _angle_to_vertical + epsilon
                else:
                    beta_eff = _angle_to_vertical + epsilon - pi
            #print 'Beta effective: ', beta_eff
            #Make correction to ejecta direction needed if angle to normal is small and slope is large in the opposite direction:
            if 0. <= beta_eff <= 90.:
                _ejecta_azimuth = _azimuth_of_travel
                break
            elif beta_eff < 0.:
                #reverse the azimuth, make beta positive again
                beta_eff = -beta_eff
                _ejecta_azimuth = (_azimuth_of_travel+pi)%twopi
                break
            else:
                print 'Impact geometry was not possible! Refreshing the impactor angle...'
                self.set_impactor_angles()
                _azimuth_of_travel = self._azimuth_of_travel
                _angle_to_vertical = self._angle_to_vertical

        #apply correction to beta to suppress nonlinear BC problem and make ejecta patterns "look like" they actually do:
        tan_beta = tan(beta_eff*self._beta_factor)
        tan_beta_sqd = tan_beta*tan_beta
        #print 'Impact, ejecta azimuths: ', _azimuth_of_travel, _ejecta_azimuth

        unique_expression_for_local_thickness = self.create_lambda_fn_for_ejecta_thickness()
        thickness_at_rim = unique_expression_for_local_thickness(_radius)
        
        #The center is transposed by rmax*tan(beta) in the direction of impact:
        #Might be an issue here - we use the rmax, so doesn't the *position* of the max thickness then also depend on rmax??
        #Should it actually just be r? -> no, use the MEAN: see Furbish para 27? For now, just use the max radius - this will be conservative, at least.
        #solve the ejecta radial thickness equ w. the min thickness to get the max radius:
        max_radius_ejecta_on_flat = _radius * (thickness_at_rim/self._minimum_ejecta_thickness)**0.3636 #+ 0.5*grid.dx #0.5dx added as a conservative buffer, as we are going to use the closest node as the center... Now removed as we don't use this method
        #print 'thickness_at_rim: ', thickness_at_rim
        #print 'max_radius_ejecta: ', max_radius_ejecta_on_flat
        
        displacement_distance = max_radius_ejecta_on_flat*tan_beta
        #need a variable for if the exacavated radius is ever outside the locus defined by the MREOF around the footprint center:
        excess_excavation_radius = (max_radius_ejecta_on_flat - displacement_distance) < _radius
        
        footprint_center_x = self._xcoord+sin(_azimuth_of_travel)*displacement_distance
        footprint_center_y = self._ycoord+cos(_azimuth_of_travel)*displacement_distance
        
        #This material assumed we could construct a distance map for all nodes, but it's too big.
        #Instead, we're going to simplify the footprint to a rectangle, not circle, to accelerate the search.
        # => added self.create_square_footprint() below; used it beneath this comment block.
        #if 0. < footprint_center_x < (grid.get_grid_xdimension()-grid.dx) and 0. < footprint_center_y < (grid.get_grid_ydimension()-grid.dx):
        #    closest_node_to_footprint_center = grid.snap_coords_to_grid(footprint_center_x, footprint_center_y)
        #    footprint_nodes = self.all_node_distances_map[closest_node_to_footprint_center,:] < max_radius_ejecta_on_flat
        #else:
        #    footprint_nodes = grid.get_distances_of_nodes_to_point((footprint_center_x, footprint_center_y)) < max_radius_ejecta_on_flat
        #if excess_excavation_radius:
        #    dist_to_center = self.all_node_distances_map[self.closest_node_index,:]        
        #    footprint_nodes = numpy.logical_or(dist_to_center<_radius, footprint_nodes)

        footprint_nodes = self.create_square_footprint((footprint_center_x,footprint_center_y),max_radius_ejecta_on_flat)
        if excess_excavation_radius:
            print 'A low-angle crater!'
            cavity_footprint = self.create_square_footprint((self._xcoord,self._ycoord),_radius)
            footprint_nodes = numpy.unique(numpy.concatenate((footprint_nodes,cavity_footprint))) #combine the two footprints into array of unique IDs
                
        _vec_r_to_center, _vec_theta = grid.get_distances_of_nodes_to_point((self._xcoord,self._ycoord), get_az='angles', node_subset=footprint_nodes)
        #Disabled, 112713
        #slope_offsets_rel_to_center = _vec_r_to_center*numpy.tan(self._surface_slope)*numpy.cos(_vec_theta-self._surface_dip_direction)
        
        #Define a shortcut identity for the elev[footprint_nodes] patch, so we don't have to keep looking it up:
        elevs_under_footprint = elev[footprint_nodes]
        ##make a local copy of the elevations under the footprint, so we can do a mass balance at the end:
        #old_elevs_under_footprint = numpy.copy(elevs_under_footprint)
        #NO! This deep copying creates runaway memory allocation through loops. Better is:
        old_elevs_under_footprint = elevs_under_footprint.copy()
        
        ##We need to account for deposition depth elevating the crater rim, i.e., we need to deposit *before* we cut the cavity. We do this by defining three domains for the node to lie in: 1. r<r_calc, i.e., below the pre-impact surface. No risk of intersecting the surface here. 2. r_calc < r; Th>z_new. this is the domain in the inward sloping rim of the crater ejecta. 3. Th<z_new and beyond. out on the ejecta proper. Note - (1) is not hard & fast rule if the surface dips. Safer is just (Th-lowering)<z_new
        ##So, calc the excavation depth for all nodes, just to be on the safe side for strongly tilted geometries:
        #(_vec_new_z is the depth that would be excavated inside the cavity, including projected depths ouside the cavity.
        _vec_new_z = numpy.empty_like(_vec_r_to_center)
        _nodes_within_crater = _vec_r_to_center<=_radius
        _nodes_outside_crater = numpy.logical_not(_nodes_within_crater)
        _vec_new_z.fill(self.closest_node_elev + thickness_at_rim - self._depth)
        #Disabled, 112713
        #_vec_new_z[_nodes_within_crater] += self._depth * (_vec_r_to_center[_nodes_within_crater]/_radius)**crater_bowl_exp - slope_offsets_rel_to_center[_nodes_within_crater]
        #_vec_new_z[_nodes_outside_crater] += self._depth + (_vec_r_to_center[_nodes_outside_crater]-_radius)*tan_repose - slope_offsets_rel_to_center[_nodes_outside_crater]
        _vec_new_z[_nodes_within_crater] += self._depth * (_vec_r_to_center[_nodes_within_crater]/_radius)**crater_bowl_exp
        _vec_new_z[_nodes_outside_crater] += self._depth + (_vec_r_to_center[_nodes_outside_crater]-_radius)*tan_repose
        _nodes_below_surface = _vec_new_z<elevs_under_footprint
        _nodes_above_surface = numpy.logical_not(_nodes_below_surface)
        #Set the ground elev for below ground nodes
        elevs_under_footprint[_nodes_below_surface] = _vec_new_z[_nodes_below_surface]
        #From here on, our new arrays will only be as long as nodes_above_surface
        _vec_flat_thickness_above_surface = unique_expression_for_local_thickness(_vec_r_to_center[_nodes_above_surface])
        _vec_theta_eff = _ejecta_azimuth - _vec_theta[_nodes_above_surface] #This is the angle of the center-to-active-node line to the azimuth along which the ejecta is concentrated
        _vec_sin_theta_sqd = sin(_vec_theta_eff) ** 2.
        #_vec_cos_theta = cos(_vec_theta_eff)
        
        #This material is not necessary as part of this footprint-based method, as we already forbid beta_factor>0.5
        ##REMEMBER, as tan_beta gets >1, the function describing the ejecta is only valid over ever more restricted ranges of theta!! In other words,
        #nodes_inside_ejecta = where(_vec_sin_theta_sqd*tan_beta_sqd <= 1.) #these are indices to an array of length nodes_above_surface only
        #_vec_thickness = numpy.zeros(nodes_above_surface.size) #i.e., it's zero outside the ejecta
        #_vec_mu_theta_by_mu0 = tan_beta * _vec_cos_theta + sqrt(1. - _vec_sin_theta_sqd * tan_beta_sqd)
        #_vec_f_theta = (tan_beta_sqd*(_vec_cos_theta**2.-_vec_sin_theta_sqd) + 2.*tan_beta*_vec_cos_theta*sqrt(1.-tan_beta_sqd*_vec_sin_theta_sqd) + 1.) / twopi
        #So, distn_at_angle = distn_vertical_impact*f_theta/mu_theta_by_mu0. Draw the thickness at the active node:
        #NB-the 2pi is to correct for mismatch in the dimensions of mu and f
        _vec_thickness = _vec_flat_thickness_above_surface
        #Set the thicknesses <0 to 0:
        _vec_thickness_positive = where(_vec_thickness>=0.,_vec_thickness, 0.)
        #Now, are we inside or outside the rim?
        elevs_under_footprint[_nodes_above_surface] = where(_vec_new_z[_nodes_above_surface]<=(elevs_under_footprint[_nodes_above_surface]+_vec_thickness_positive),_vec_new_z[_nodes_above_surface], elevs_under_footprint[_nodes_above_surface]+_vec_thickness_positive)

        #Save any data to the higher level:      
        self.elev[footprint_nodes] = elevs_under_footprint
        elev_diff = elevs_under_footprint-old_elevs_under_footprint
        self.mass_balance_in_impact = numpy.sum(elev_diff)/-numpy.sum(elev_diff[elev_diff<0.]) #positive is mass gain, negative is mass loss. This is currently a mass fraction, given relative to volume (h*px#) excavated from below the original surface.
        self.ejecta_azimuth = _ejecta_azimuth
        self.impactor_angle_to_surface_normal = beta_eff #note in this case this is the *effective* angle (in the direction of travel), not the actual angle to the surface.
        #print 'Vol of crater cavity: ', self._cavity_volume
        
        #Uncomment this line to see which nodes are under the footprint:
        #self.elev[footprint_nodes] = 10.
        #self.elev[footprint_nodes[_nodes_within_crater]] = 20.
        
        del(_vec_r_to_center)
        del(_vec_theta)
        del(_vec_new_z)
        del(_nodes_below_surface)
        del(_nodes_above_surface)
        del(_vec_sin_theta_sqd)
        #del(_vec_mu_theta_by_mu0)
        #del(_vec_f_theta)
        del(_vec_thickness_positive)
        del(elevs_under_footprint)
        del(elev_diff)
        
        return self.elev

    def set_elev_change_only_beneath_footprint_no_angular_BAND_AID(self):
        '''
        This method is in all respects equivalent to the above similarly named 
        code, but it does not use either impact or surface angles in drawing the
        impact ejecta. The impact ejecta field is always radially symmetric 
        about the impact point.
        '''     
        #Load in the data, for speed and conciseness:
        _angle_to_vertical=self._angle_to_vertical
        _surface_slope=self._surface_slope
        _surface_dip_direction = self._surface_dip_direction
        _azimuth_of_travel = self._azimuth_of_travel
        pi = numpy.pi
        twopi = 2.*pi
        tan = numpy.tan
        cos = numpy.cos
        sin = numpy.sin
        sqrt = numpy.sqrt
        #arctan = numpy.arctan
        arccos = numpy.arccos
        where = numpy.where
        _radius = self._radius
        elev = self.elev
        tan_repose = self.tan_repose
        grid = self.grid
        self.cheater_flag = 0
        
        #Derive the exponent for the crater shape, shared betw simple & complex:
        crater_bowl_exp = self.get_crater_shape_exp()
        
        #Derive the effective angle for impact, relative to the surface normal, beta_eff. The direction is always controlled by the impactor. Draw new impact angles if impact geomtery is impossible.
        while 1:
            #epsilon is the angle between the surface normal and the impactor angle to vertical, projected along the line of travel.
            rake_in_surface_plane = _surface_dip_direction - _azimuth_of_travel
            #print '_surface_dip_direction: ', _surface_dip_direction
            #print '_azimuth_of_travel: ', _azimuth_of_travel
            #print '_angle_to_vertical: ', _angle_to_vertical
            absolute_rake = numpy.fabs(rake_in_surface_plane)
            if not _surface_slope:
                epsilon = 0.
                beta_eff = _angle_to_vertical
            else:
                if absolute_rake in (0.5*pi, 1.5*pi):
                    epsilon = 0.
                else:
                    epsilon = arccos(cos(rake_in_surface_plane)*cos(_surface_slope)*sqrt(1.+(tan(rake_in_surface_plane)/cos(_surface_slope))**2.))
                #Make the necessary adjustment to the angle to vertical to reflect dipping surface:
                if absolute_rake <= 0.5*pi or absolute_rake >= 1.5*pi:
                    beta_eff = _angle_to_vertical + epsilon
                else:
                    beta_eff = _angle_to_vertical + epsilon - pi
            #print 'Beta effective: ', beta_eff
            #Make correction to ejecta direction needed if angle to normal is small and slope is large in the opposite direction:
            if 0. <= beta_eff <= 90.:
                _ejecta_azimuth = _azimuth_of_travel
                break
            elif beta_eff < 0.:
                #reverse the azimuth, make beta positive again
                beta_eff = -beta_eff
                _ejecta_azimuth = (_azimuth_of_travel+pi)%twopi
                break
            else:
                #Note this situation should now be forbidden as we correct for grazing impacts
                print 'Impact geometry was not possible! Refreshing the impactor angle...'
                self.set_impactor_angles()
                _azimuth_of_travel = self._azimuth_of_travel
                _angle_to_vertical = self._angle_to_vertical

        #apply correction to beta to suppress nonlinear BC problem and make ejecta patterns "look like" they actually do:
        tan_beta = tan(beta_eff*self._beta_factor)
        tan_beta_sqd = tan_beta*tan_beta
        #print 'Impact, ejecta azimuths: ', _azimuth_of_travel, _ejecta_azimuth
        
        ###Here's the oh-so-ugly ad-hoc fix to address the mass balance problems
        #We're calculating the approx excavated volume directly to feed the ejecta thickness calculator...
        excavation_nodes = self.create_square_footprint((self._xcoord,self._ycoord),4.*_radius) #Arbitrary increase in radius to try to catch the additional excavated material
        #sharp lips w/i the grid are CATASTROPHIC (=>get super-steep slopes) - this 4* really has to be sufficient.
        _vec_r_to_center_excav,_vec_theta_excav = grid.get_distances_of_nodes_to_point((self._xcoord,self._ycoord), get_az='angles', node_subset=excavation_nodes)
        slope_offsets_rel_to_center_excav = -_vec_r_to_center_excav*numpy.tan(self._surface_slope)*numpy.cos(_vec_theta_excav-self._surface_dip_direction) #node leading negative - downslopes are +ve!!
        _vec_new_z_excav = slope_offsets_rel_to_center_excav + self.closest_node_elev - 1.*self._depth #Arbitrary assumed scaling: rim is 0.1 of total depth
        _vec_new_z_excav += self._depth * (_vec_r_to_center_excav/_radius)**crater_bowl_exp #note there's another fix here - we've relaxed the constraint that slopes outside one radius are at repose. It will keep curling up, so poke out quicker
        z_difference = self.elev[excavation_nodes] - _vec_new_z_excav #+ve when excavating
        excavated_volume = numpy.sum(numpy.where(z_difference>0.,z_difference,0.))*grid.dx*grid.dx
        #This all assumes we're representing the surface slope accurately now. If we are, then the real radius will remain roughly as the calculated value as we shear the crater into the surface. If not, could have problems.
        
        unique_expression_for_local_thickness = self.create_lambda_fn_for_ejecta_thickness_BAND_AID(excavated_volume)
        thickness_at_rim = unique_expression_for_local_thickness(_radius)
        if thickness_at_rim<0.:
            thickness_at_rim = 0.
        #print 'Surface_slope: ', _surface_slope
        #print 'Depth: ', self._depth
        #print 'Rim thickness: ', thickness_at_rim
        #####
        
        #The center is transposed by rmax*tan(beta) in the direction of impact:
        #Might be an issue here - we use the rmax, so doesn't the *position* of the max thickness then also depend on rmax??
        #Should it actually just be r? -> no, use the MEAN: see Furbish para 27? For now, just use the max radius - this will be conservative, at least.
        #solve the ejecta radial thickness equ w. the min thickness to get the max radius:
        max_radius_ejecta_on_flat = _radius * (thickness_at_rim/self._minimum_ejecta_thickness)**0.3636 #+ 0.5*grid.dx #0.5dx added as a conservative buffer, as we are going to use the closest node as the center... Now removed as we don't use this method
        #print 'thickness_at_rim: ', thickness_at_rim
        #print 'max_radius_ejecta: ', max_radius_ejecta_on_flat
        
        displacement_distance = max_radius_ejecta_on_flat*tan_beta
        #need a variable for if the exacavated radius is ever outside the locus defined by the MREOF around the footprint center:
        excess_excavation_radius = (max_radius_ejecta_on_flat - displacement_distance) < 4.*_radius
        
        footprint_center_x = self._xcoord+sin(_azimuth_of_travel)*displacement_distance
        footprint_center_y = self._ycoord+cos(_azimuth_of_travel)*displacement_distance
        
        #This material assumed we could construct a distance map for all nodes, but it's too big.
        #Instead, we're going to simplify the footprint to a rectangle, not circle, to accelerate the search.
        # => added self.create_square_footprint() below; used it beneath this comment block.
        #if 0. < footprint_center_x < (grid.get_grid_xdimension()-grid.dx) and 0. < footprint_center_y < (grid.get_grid_ydimension()-grid.dx):
        #    closest_node_to_footprint_center = grid.snap_coords_to_grid(footprint_center_x, footprint_center_y)
        #    footprint_nodes = self.all_node_distances_map[closest_node_to_footprint_center,:] < max_radius_ejecta_on_flat
        #else:
        #    footprint_nodes = grid.get_distances_of_nodes_to_point((footprint_center_x, footprint_center_y)) < max_radius_ejecta_on_flat
        #if excess_excavation_radius:
        #    dist_to_center = self.all_node_distances_map[self.closest_node_index,:]        
        #    footprint_nodes = numpy.logical_or(dist_to_center<_radius, footprint_nodes)

        footprint_nodes = self.create_square_footprint((footprint_center_x,footprint_center_y),max_radius_ejecta_on_flat)
        if excess_excavation_radius:
            print 'A low-angle crater!'
            cavity_footprint = self.create_square_footprint((self._xcoord,self._ycoord),5.*_radius)
            footprint_nodes = numpy.unique(numpy.concatenate((footprint_nodes,cavity_footprint))) #combine the two footprints into array of unique IDs
        
        _vec_r_to_center, _vec_theta = grid.get_distances_of_nodes_to_point((self._xcoord,self._ycoord), get_az='angles', node_subset=footprint_nodes)
        
        #Define a shortcut identity for the elev[footprint_nodes] patch, so we don't have to keep looking it up:
        elevs_under_footprint = elev[footprint_nodes]
        ##make a local copy of the elevations under the footprint, so we can do a mass balance at the end:
        #old_elevs_under_footprint = numpy.copy(elevs_under_footprint)
        #NO! This deep copying creates runaway memory allocation through loops. Better is:
        old_elevs_under_footprint = elevs_under_footprint.copy()
        
        ##We need to account for deposition depth elevating the crater rim, i.e., we need to deposit *before* we cut the cavity. We do this by defining three domains for the node to lie in: 1. r<r_calc, i.e., below the pre-impact surface. No risk of intersecting the surface here. 2. r_calc < r; Th>z_new. this is the domain in the inward sloping rim of the crater ejecta. 3. Th<z_new and beyond. out on the ejecta proper. Note - (1) is not hard & fast rule if the surface dips. Safer is just (Th-lowering)<z_new
        ##So, calc the excavation depth for all nodes, just to be on the safe side for strongly tilted geometries:
        #(_vec_new_z is the depth that would be excavated inside the cavity, including projected depths ouside the cavity.
        _nodes_within_crater = _vec_r_to_center<=_radius
        _nodes_outside_crater = numpy.logical_not(_nodes_within_crater)
        _vec_new_z = -_vec_r_to_center*numpy.tan(self._surface_slope)*numpy.cos(_vec_theta-self._surface_dip_direction) + self.closest_node_elev + thickness_at_rim - self._depth
        _vec_new_z[_nodes_within_crater] += self._depth * (_vec_r_to_center[_nodes_within_crater]/_radius)**crater_bowl_exp
        _vec_new_z[_nodes_outside_crater] += self._depth + (_vec_r_to_center[_nodes_outside_crater]-_radius)*tan_repose
        _nodes_below_surface = _vec_new_z<elevs_under_footprint
        _nodes_above_surface = numpy.logical_not(_nodes_below_surface)
        _vec_theta_eff = _ejecta_azimuth - _vec_theta[_nodes_above_surface] #This is the angle of the center-to-active-node line to the azimuth along which the ejecta is concentrated

        #Set the ground elev for below ground nodes
        elevs_under_footprint[_nodes_below_surface] = _vec_new_z[_nodes_below_surface]
        #From here on, our new arrays will only be as long as nodes_above_surface
        _vec_flat_thickness_above_surface = unique_expression_for_local_thickness(_vec_r_to_center[_nodes_above_surface])
        _vec_sin_theta_sqd = sin(_vec_theta_eff) ** 2.
        _vec_cos_theta = cos(_vec_theta_eff)
        
        #This material is not necessary as part of this footprint-based method, as we already forbid beta_factor>0.5
        ##REMEMBER, as tan_beta gets >1, the function describing the ejecta is only valid over ever more restricted ranges of theta!! In other words,
        #nodes_inside_ejecta = where(_vec_sin_theta_sqd*tan_beta_sqd <= 1.) #these are indices to an array of length nodes_above_surface only
        #_vec_thickness = numpy.zeros(nodes_above_surface.size) #i.e., it's zero outside the ejecta
        #_vec_mu_theta_by_mu0 = tan_beta * _vec_cos_theta + sqrt(1. - _vec_sin_theta_sqd * tan_beta_sqd)
        #_vec_f_theta = (tan_beta_sqd*(_vec_cos_theta**2.-_vec_sin_theta_sqd) + 2.*tan_beta*_vec_cos_theta*sqrt(1.-tan_beta_sqd*_vec_sin_theta_sqd) + 1.) / twopi
        #So, distn_at_angle = distn_vertical_impact*f_theta/mu_theta_by_mu0. Draw the thickness at the active node:
        #NB-the 2pi is to correct for mismatch in the dimensions of mu and f
        _vec_thickness = _vec_flat_thickness_above_surface
        #print self._xcoord, self._ycoord
        #print 'angle to vertical', self._angle_to_vertical
        #print 'effective beta, impactor dir of travel: ', beta_eff, self._azimuth_of_travel
        #if len(_vec_thickness) == 0:
        #    print self._radius
        #    print self._surface_dip_direction
        #    print self._surface_slope
        #    print len(footprint_nodes)
        #    print len(excavation_nodes)
        #    input()
        #Set the thicknesses <0 to 0:
        _vec_thickness_positive = where(_vec_thickness>=0.,_vec_thickness, 0.)
        #Now, are we inside or outside the rim?
        elevs_under_footprint[_nodes_above_surface] = where(_vec_new_z[_nodes_above_surface]<=(elevs_under_footprint[_nodes_above_surface]+_vec_thickness_positive),_vec_new_z[_nodes_above_surface], elevs_under_footprint[_nodes_above_surface]+_vec_thickness_positive)
        elev_diff = elevs_under_footprint-old_elevs_under_footprint
        init_mass_balance = numpy.sum(elev_diff)/-numpy.sum(elev_diff[elev_diff<0.])
        #add this method to try to kill the instability resulting from ever-increasing slopes:
        if init_mass_balance < -0.9:
            print 'ignoring slope effect for this impact...'
            self.cheater_flag = 1
            #footprint_nodes = numpy.arange(grid.number_of_nodes,dtype=int) #all the nodes, this time
            #previous version of footprint_nodes should suffice
            _vec_r_to_center, _vec_theta = grid.get_distances_of_nodes_to_point((self._xcoord,self._ycoord), get_az='angles', node_subset=footprint_nodes)
            elevs_under_footprint = elev[footprint_nodes]
            old_elevs_under_footprint = elevs_under_footprint.copy()
            _nodes_within_crater = _vec_r_to_center<=_radius
            _nodes_outside_crater = numpy.logical_not(_nodes_within_crater)
            _vec_new_z = numpy.empty_like(_vec_theta)
            #Now, we DON'T incorporate the surface tilting. Trying to reset local slopes to repose.
            _vec_new_z.fill(self.closest_node_elev + thickness_at_rim - self._depth)
            _vec_new_z[_nodes_within_crater] += self._depth * (_vec_r_to_center[_nodes_within_crater]/_radius)**crater_bowl_exp
            _vec_new_z[_nodes_outside_crater] += self._depth + (_vec_r_to_center[_nodes_outside_crater]-_radius)*tan_repose
            _nodes_below_surface = _vec_new_z<elevs_under_footprint
            _nodes_above_surface = numpy.logical_not(_nodes_below_surface)
            _vec_theta_eff = _ejecta_azimuth - _vec_theta[_nodes_above_surface] #This is the angle of the center-to-active-node line to the azimuth along which the ejecta is concentrated
            elevs_under_footprint[_nodes_below_surface] = _vec_new_z[_nodes_below_surface]
            _vec_flat_thickness_above_surface = unique_expression_for_local_thickness(_vec_r_to_center[_nodes_above_surface])
            _vec_sin_theta_sqd = sin(_vec_theta_eff) ** 2.
            _vec_cos_theta = cos(_vec_theta_eff)
            _vec_mu_theta_by_mu0 = tan_beta * _vec_cos_theta + sqrt(1. - _vec_sin_theta_sqd * tan_beta_sqd)
            _vec_f_theta = (tan_beta_sqd*(_vec_cos_theta**2.-_vec_sin_theta_sqd) + 2.*tan_beta*_vec_cos_theta*sqrt(1.-tan_beta_sqd*_vec_sin_theta_sqd) + 1.) / twopi
            _vec_thickness = _vec_f_theta/_vec_mu_theta_by_mu0 * twopi * _vec_flat_thickness_above_surface
            _vec_thickness_positive = where(_vec_thickness>=0.,_vec_thickness, 0.)
            elevs_under_footprint[_nodes_above_surface] = where(_vec_new_z[_nodes_above_surface]<=(elevs_under_footprint[_nodes_above_surface]+_vec_thickness_positive),_vec_new_z[_nodes_above_surface], elevs_under_footprint[_nodes_above_surface]+_vec_thickness_positive)
            elev_diff = elevs_under_footprint-old_elevs_under_footprint
            init_mass_balance = numpy.sum(elev_diff)/-numpy.sum(elev_diff[elev_diff<0.])

        #Save any data to the higher level:      
        self.elev[footprint_nodes] = elevs_under_footprint
        elev_diff = elevs_under_footprint-old_elevs_under_footprint
        self.mass_balance_in_impact = numpy.sum(elev_diff)/-numpy.sum(elev_diff[elev_diff<0.]) #positive is mass gain, negative is mass loss. This is currently a mass fraction, given relative to volume (h*px#) excavated from below the original surface.
        self.ejecta_azimuth = _ejecta_azimuth
        self.impactor_angle_to_surface_normal = beta_eff #note in this case this is the *effective* angle (in the direction of travel), not the actual angle to the surface.
        #print 'Vol of crater cavity: ', self._cavity_volume
        
        #Uncomment this line to see which nodes are under the footprint:
        #data.elev[footprint_nodes] = 10.
        
        return self.elev



    def create_square_footprint(self, center, eff_radius):
        '''
        This method creates a square footprint of nodes around a given center
        point, with a specified halfwidth.
        It is designed to avoid the need to actually search the whole grid
        in order to establish the footprint, to accelerate the craters
        module.
        It should be able to handle footprints which fall off the edge of the 
        grid, but can't yet handle looped boundaries.
        "Center" is a tuple, (x,y).
        '''
        assert type(center) == tuple
        assert len(center) == 2
        center_array = numpy.array(center)
        dx = self.grid.dx
        max_cols = self.grid.number_of_node_columns
        max_rows = self.grid.number_of_node_rows
        left_bottom = ((center_array - eff_radius)//dx).astype(int) + 1
        right_top_nonzero = ((center_array + eff_radius)//dx).astype(int)

        left_bottom_nonzero = numpy.where(left_bottom<0,0,left_bottom)
        if right_top_nonzero[0]>(max_cols-1):
            right_top_nonzero[0] = max_cols-1
        if right_top_nonzero[1]>(max_rows-1):
            right_top_nonzero[1] = max_rows-1
                    
        x = numpy.arange(right_top_nonzero[0]-left_bottom_nonzero[0]+1) + left_bottom_nonzero[0]
        y = numpy.arange(right_top_nonzero[1]-left_bottom_nonzero[1]+1) + left_bottom_nonzero[1]
        y_column = y.reshape((y.shape[0],1))
        footprint_nodes_2dim = x + y_column*self.grid.number_of_node_columns
        return footprint_nodes_2dim.flatten()

    #@profile
    def excavate_a_crater_noangle(self, grid):
        '''
        This method executes the most of the other methods of this crater
        class, and makes the geomorphic changes to a mesh associated with a
        single bolide impact with randomized properties. It receives and works
        on the data fields attached to the model grid. 
        This version calls the _no_angle() method above, and thus produces
        always radially symmetric distributions.
        ***This is one of the primary interface method of this class.***
        '''
        self.grid = grid
        self.elev = grid.at_node['planet_surface__elevation']
        self.draw_new_parameters()
        #These get updated in set_crater_mean_slope_v3()
        self.closest_node_index = grid.snap_coords_to_grid(self._xcoord, self._ycoord)
        self.closest_node_elev = self.elev[self.closest_node_index]
        self.check_coords_and_angles_for_grazing()
        self.set_crater_mean_slope_v3()
        self.set_depth_from_size()
        self.set_crater_volume()

        if self._radius !=1e-6:
            if numpy.isnan(self._surface_slope):
                print 'Surface slope is not defined for this crater! Is it too big? Crater will not be drawn.'
            else:
                self.set_elev_change_only_beneath_footprint_no_angular_BAND_AID()
            #print 'Impactor angle to ground normal: ', self.impactor_angle_to_surface_normal
            print 'Mass balance in impact: ', self.mass_balance_in_impact
            #print '*****'
            #Record the data:
            #Is this making copies, or just by reference? Check output. Should be copies, as these are floats, not more complex objects.
            self.impact_property_dict = {'x': self._xcoord, 'y': self._ycoord, 'r': self._radius, 'volume': self._cavity_volume, 'surface_slope': self._surface_slope, 'normal_angle': self.impactor_angle_to_surface_normal, 'impact_az': self._azimuth_of_travel, 'ejecta_az': self.ejecta_azimuth, 'mass_balance': self.mass_balance_in_impact, 'redug_crater': self.cheater_flag}
        else:
            self.impact_property_dict = {'x': -1., 'y': -1., 'r': -1., 'volume': -1., 'surface_slope': -1., 'normal_angle': -1., 'impact_az': -1., 'ejecta_az': -1., 'mass_balance': -1., 'redug_crater': -1}
        return self.grid

        
    def excavate_a_crater_furbish(self, grid):
        '''
        This method executes the most of the other methods of this crater
        class, and makes the geomorphic changes to a mesh associated with a
        single bolide impact with randomized properties. It receives and works
        on the data fields attached to the model grid. 
        This version implements the full, angle dependent versions of the
        methods, following Furbish et al., 2007.
        ***This is one of the primary interface method of this class.***
        '''
        self.grid = grid
        self.elev = grid.at_node['planet_surface__elevation']
        self.draw_new_parameters()
        #These get updated in set_crater_mean_slope_v3()
        self.closest_node_index = grid.snap_coords_to_grid(self._xcoord, self._ycoord)
        self.closest_node_elev = self.elev[self.closest_node_index]
        self.check_coords_and_angles_for_grazing()
        self.set_crater_mean_slope_v3()
        self.set_depth_from_size()
        self.set_crater_volume()
        #print 'Surface slope: ', self._surface_slope
        #print 'Dip dir: ', self._surface_dip_direction
        
        if self._radius !=1e-6:
            if numpy.isnan(self._surface_slope):
                print 'Surface slope is not defined for this crater! Is it too big? Crater will not be drawn.'
            else:
                self.set_elev_change_only_beneath_footprint_BAND_AID()
            #print 'Impactor angle to ground normal: ', self.impactor_angle_to_surface_normal
            print 'Mass balance in impact: ', self.mass_balance_in_impact
            if self.mass_balance_in_impact<-0.9:
                print 'radius: ', self._radius
                print 'location: ', self._xcoord, self._ycoord
                print 'surface dip dir: ', self._surface_dip_direction
                print 'surface slope: ', self._surface_slope
                print 'travel azimuth: ', self._azimuth_of_travel
                print 'angle to normal: ', self.impactor_angle_to_surface_normal
                #pylab.imshow(self.grid.node_vector_to_raster(self.elev))
                #pylab.colorbar()
                #pylab.show()
            print '*****'
            #Record the data:
            #Is this making copies, or just by reference? Check output. Should be copies, as these are floats, not more complex objects.
            self.impact_property_dict = {'x': self._xcoord, 'y': self._ycoord, 'r': self._radius, 'volume': self._cavity_volume, 'surface_slope': self._surface_slope, 'normal_angle': self.impactor_angle_to_surface_normal, 'impact_az': self._azimuth_of_travel, 'ejecta_az': self.ejecta_azimuth, 'mass_balance': self.mass_balance_in_impact, 'redug_crater': self.cheater_flag}
        else:
            self.impact_property_dict = {'x': -1., 'y': -1., 'r': -1., 'volume': -1., 'surface_slope': -1., 'normal_angle': -1., 'impact_az': -1., 'ejecta_az': -1., 'mass_balance': -1., 'redug_crater': -1}
        return self.grid