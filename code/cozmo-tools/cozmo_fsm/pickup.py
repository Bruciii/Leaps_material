from cozmo.util import Pose
from cozmo.objects import LightCube

from .nodes import *
from .transitions import *
from .transform import wrap_angle
from .pilot import PilotToPose, PilotCheckStart, ParentPilotEvent, StartCollides, InvalidPose
from .worldmap import LightCubeObj
from .doorpass import WallPilotToPose
from .trace import tracefsm

from math import sin, cos, atan2, pi, sqrt

class GoToCube(StateNode):
    def __init__(self, cube=None):
        self.object = cube
        super().__init__()

    def start(self, event=None):
        # self.object will normally be set up by the parent of this node
        if isinstance(self.object, LightCube):
            self.wmobject = self.robot.world.world_map.objects[self.object]
        elif isinstance(self.object, LightCubeObj):
            self.wmobject = self.object
            self.object = self.object.sdk_obj
        else:
            raise ValueError(self.object)
        self.children['looker'].object = self.object
        super().start(event)
        if self.wmobject.pose_confidence < 0:
            print('GoToCube: cube has invalid pose!', self.wmobject, self.object.pose)
            self.post_event(PilotEvent(InvalidPose))
            self.post_failure()

    def pick_side(self, dist, use_world_map):
        "*** NOTE: This code is only correct for upright cubes"
        cube = self.object
        if use_world_map:
            wobj = self.robot.world.world_map.objects[cube]
            x = wobj.x
            y = wobj.y
            ang = wobj.theta
            rx = self.robot.world.particle_filter.pose[0]
            ry = self.robot.world.particle_filter.pose[1]
        else:
            x = cube.pose.position.x
            y = cube.pose.position.y
            ang = cube.pose.rotation.angle_z.radians
            rx = self.robot.pose.position.x
            ry = self.robot.pose.position.y
        side1 = [ (x + cos(ang)*dist), (y + sin(ang)*dist), ang + pi   ]
        side2 = [ (x - cos(ang)*dist), (y - sin(ang)*dist), ang        ]
        side3 = [ (x + sin(ang)*dist), (y - cos(ang)*dist), ang + pi/2 ]
        side4 = [ (x - sin(ang)*dist), (y + cos(ang)*dist), ang - pi/2 ]
        sides = (side1, side2, side3, side4)
        sorted_sides = sorted(sides, key=lambda pt: (pt[0]-rx)**2 + (pt[1]-ry)**2)
        return sorted_sides[0]

    def almost_docked(self, side, use_world_map):
        """Returns True if we're almost docked with the cube so we don't
        need to check for collisions."""
        if use_world_map:
            rx = self.robot.world.particle_filter.pose[0]
            ry = self.robot.world.particle_filter.pose[1]
            rtheta = self.robot.world.particle_filter.pose[2]
        else:
            rx = self.robot.pose.position.x
            ry = self.robot.pose.position.y
            rtheta = self.robot.pose.rotation.angle_z.radians
        dist = math.sqrt((rx-side[0])**2 + (ry-side[1])**2)
        relative_angle = abs(wrap_angle(rtheta-side[2]) % (pi/2)) * (180/pi)
        return (dist < 100) and (relative_angle < 10)

    class GoToSide(PilotToPose):
        def __init__(self):
            super().__init__(None)

        def start(self, event=None):
            cube = self.parent.object
            (x, y, theta) = self.parent.pick_side(100, use_world_map=True)
            self.target_pose = Pose(x, y, self.robot.pose.position.z,
                                    angle_z=Angle(radians=wrap_angle(theta)))
            (px,py,pq) = self.robot.world.particle_filter.pose
            print('GoToSide: planned path from (%.1f, %.1f) @ %.1f deg. to pickup point (%.1f, %.1f) @ %.1f deg.' %
                  (px, py, pq*180/pi,
                   self.target_pose.position.x, self.target_pose.position.y,
                   self.target_pose.rotation.angle_z.degrees))
            super().start(event)

    class ReportPosition(StateNode):
        def __init__(self,id=None):
            super().__init__()
            self.id_string = id + ': ' if id else ''

        def start(self,event=None):
            super().start(event)
            cube = self.parent.object
            vis = 'visible' if cube.is_visible else 'not visible'
            cx = cube.pose.position.x
            cy = cube.pose.position.y
            rx = self.robot.pose.position.x
            ry = self.robot.pose.position.y
            dx = cx - rx
            dy = cy - ry
            dist = math.sqrt(dx*dx + dy*dy)
            bearing = wrap_angle(atan2(dy,dx) - self.robot.pose.rotation.angle_z.radians) * 180/pi
            print('%scube %s at (%5.1f,%5.1f)  robot at (%5.1f,%5.1f)  dist=%5.1f  rel. brg=%5.1f' %
                  (self.id_string, vis, cx, cy, rx, ry, dist, bearing))


    class TurnToCube(Turn):
        def __init__(self, check_vis=False):
            self.check_vis = check_vis
            super().__init__()

        def start(self, event=None):
            if self.running: return
            cube = self.parent.object
            if self.check_vis and not cube.is_visible:
                print('** TurnToCube could not see the cube.')
                self.angle = None
                super().start(event)
                self.post_failure()
            else:
                (cx, cy, _) = self.parent.pick_side(0, False)
                rx = self.robot.pose.position.x
                ry = self.robot.pose.position.y
                dx = cx - rx
                dy = cy - ry
                dist = math.sqrt(dx*dx + dy*dy)
                self.angle = degrees(wrap_angle(atan2(dy,dx) - self.robot.pose.rotation.angle_z.radians) \
                                     * 180/pi)
                if abs(self.angle.degrees) <= 2:
                    self.angle = degrees(0)
                print('TurnToCube: cube at (%5.1f,%5.1f)  robot at (%5.1f,%5.1f)  dist=%5.1f  angle=%5.1f' %
                      (cx, cy, rx, ry, dist, self.angle.degrees))
                super().start(event)

    class CheckAlmostDocked(StateNode):
        def start(self, event=None):
            if self.running: return
            super().start(event)
            cube = self.parent.object
            if not cube.is_visible:
                self.post_failure()
            side = self.parent.pick_side(25, False)
            if self.parent.almost_docked(side,False):
                self.post_success()
            else:
                self.post_failure()

    class ForwardToCube(Forward):
        def __init__(self, offset):
            self.offset = offset
            super().__init__()

        def start(self, event=None):
            if self.running: return
            cube = self.parent.object
            dx = cube.pose.position.x - self.robot.pose.position.x
            dy = cube.pose.position.y - self.robot.pose.position.y
            dist = sqrt(dx*dx + dy*dy) - self.offset
            if (dist < 0):
                print('***** ForwardToCube negative distance:',dist)
            self.distance = Distance(dist)
            print('ForwardToCube: distance %.1f mm' % self.distance.distance_mm)
            super().start(event)

    def setup(self):
        """
            # GoToCube machine
            
            droplift: SetLiftHeight(0) =C=> {waitlift, looker}
            droplift =F=> {waitlift, looker}   # lift motion will fail if on charger
    
            waitlift: StateNode() =T(1)=>    # allow time for vision to set up world map
               check_almost_docked
    
            looker: LookAtObject()
    
            check_almost_docked: self.CheckAlmostDocked()
            check_almost_docked =S=> turn_to_cube2
            check_almost_docked =F=> check_start
    
            check_start: PilotCheckStart()
            check_start =S=> Print('Start collision check passed.') =N=> go_side
            # TODO: instead of blindly backing up, find the best direction to move.
            check_start =F=> Print('Backing up to escape start collision...') =N=>
               Forward(-80) =C=> StateNode() =T(0.5)=> check_start2
    
            # Second chance to avoid StartCollides.  There is no third chance.
            check_start2: PilotCheckStart()
            check_start2 =S=> Print('Start collision re-check passed.') =N=> go_side
            check_start2 =F=> ParentPilotEvent()
    
            go_side: self.GoToSide()
            go_side =PILOT=> go_side_pilot: ParentPilotEvent()
            go_side =F=> ParentFails()
            go_side =C=> self.ReportPosition('go_side_deccel')
                =T(0.75)=> self.ReportPosition('go_side_stopped')
                =N=> turn_to_cube1
    
            turn_to_cube1: self.TurnToCube(check_vis=True) =C=>
                self.ReportPosition('turn_to_cube1_deccel')
                =T(0.75)=> self.ReportPosition('turn_to_cube1_stopped')
                =N=> approach
            turn_to_cube1 =F=> Forward(-50) =C=> StateNode() =T(1)=> turn_to_cube2
    
            approach: self.ForwardToCube(60) =C=>
                self.ReportPosition('approach') =T(0.75)=>
                self.ReportPosition('approach') =N=>
                self.TurnToCube(check_vis=False) =C=> self.ForwardToCube(15) =C=> success
    
            turn_to_cube2: self.TurnToCube(check_vis=True)
            turn_to_cube2 =F=> Print("TurnToCube2: Cube Lost") =N=> ParentFails()
            turn_to_cube2 =C=> self.ForwardToCube(60) =C=> turn_to_cube3
    
            turn_to_cube3: self.TurnToCube(check_vis=False)   # can't fail
            turn_to_cube3 =C=> self.ForwardToCube(20) =C=> success
    
            success: ParentCompletes()
        """
        
        # Code generated by genfsm on Thu May 24 00:39:49 2018:
        
        droplift = SetLiftHeight(0) .set_name("droplift") .set_parent(self)
        waitlift = StateNode() .set_name("waitlift") .set_parent(self)
        looker = LookAtObject() .set_name("looker") .set_parent(self)
        check_almost_docked = self.CheckAlmostDocked() .set_name("check_almost_docked") .set_parent(self)
        check_start = PilotCheckStart() .set_name("check_start") .set_parent(self)
        print1 = Print('Start collision check passed.') .set_name("print1") .set_parent(self)
        print2 = Print('Backing up to escape start collision...') .set_name("print2") .set_parent(self)
        forward1 = Forward(-80) .set_name("forward1") .set_parent(self)
        statenode1 = StateNode() .set_name("statenode1") .set_parent(self)
        check_start2 = PilotCheckStart() .set_name("check_start2") .set_parent(self)
        print3 = Print('Start collision re-check passed.') .set_name("print3") .set_parent(self)
        parentpilotevent1 = ParentPilotEvent() .set_name("parentpilotevent1") .set_parent(self)
        go_side = self.GoToSide() .set_name("go_side") .set_parent(self)
        go_side_pilot = ParentPilotEvent() .set_name("go_side_pilot") .set_parent(self)
        parentfails1 = ParentFails() .set_name("parentfails1") .set_parent(self)
        reportposition1 = self.ReportPosition('go_side_deccel') .set_name("reportposition1") .set_parent(self)
        reportposition2 = self.ReportPosition('go_side_stopped') .set_name("reportposition2") .set_parent(self)
        turn_to_cube1 = self.TurnToCube(check_vis=True) .set_name("turn_to_cube1") .set_parent(self)
        reportposition3 = self.ReportPosition('turn_to_cube1_deccel') .set_name("reportposition3") .set_parent(self)
        reportposition4 = self.ReportPosition('turn_to_cube1_stopped') .set_name("reportposition4") .set_parent(self)
        forward2 = Forward(-50) .set_name("forward2") .set_parent(self)
        statenode2 = StateNode() .set_name("statenode2") .set_parent(self)
        approach = self.ForwardToCube(60) .set_name("approach") .set_parent(self)
        reportposition5 = self.ReportPosition('approach') .set_name("reportposition5") .set_parent(self)
        reportposition6 = self.ReportPosition('approach') .set_name("reportposition6") .set_parent(self)
        turntocube1 = self.TurnToCube(check_vis=False) .set_name("turntocube1") .set_parent(self)
        forwardtocube1 = self.ForwardToCube(15) .set_name("forwardtocube1") .set_parent(self)
        turn_to_cube2 = self.TurnToCube(check_vis=True) .set_name("turn_to_cube2") .set_parent(self)
        print4 = Print("TurnToCube2: Cube Lost") .set_name("print4") .set_parent(self)
        parentfails2 = ParentFails() .set_name("parentfails2") .set_parent(self)
        forwardtocube2 = self.ForwardToCube(60) .set_name("forwardtocube2") .set_parent(self)
        turn_to_cube3 = self.TurnToCube(check_vis=False) .set_name("turn_to_cube3") .set_parent(self)
        forwardtocube3 = self.ForwardToCube(20) .set_name("forwardtocube3") .set_parent(self)
        success = ParentCompletes() .set_name("success") .set_parent(self)
        
        completiontrans1 = CompletionTrans() .set_name("completiontrans1")
        completiontrans1 .add_sources(droplift) .add_destinations(waitlift,looker)
        
        failuretrans1 = FailureTrans() .set_name("failuretrans1")
        failuretrans1 .add_sources(droplift) .add_destinations(waitlift,looker)
        
        timertrans1 = TimerTrans(1) .set_name("timertrans1")
        timertrans1 .add_sources(waitlift) .add_destinations(check_almost_docked)
        
        successtrans1 = SuccessTrans() .set_name("successtrans1")
        successtrans1 .add_sources(check_almost_docked) .add_destinations(turn_to_cube2)
        
        failuretrans2 = FailureTrans() .set_name("failuretrans2")
        failuretrans2 .add_sources(check_almost_docked) .add_destinations(check_start)
        
        successtrans2 = SuccessTrans() .set_name("successtrans2")
        successtrans2 .add_sources(check_start) .add_destinations(print1)
        
        nulltrans1 = NullTrans() .set_name("nulltrans1")
        nulltrans1 .add_sources(print1) .add_destinations(go_side)
        
        failuretrans3 = FailureTrans() .set_name("failuretrans3")
        failuretrans3 .add_sources(check_start) .add_destinations(print2)
        
        nulltrans2 = NullTrans() .set_name("nulltrans2")
        nulltrans2 .add_sources(print2) .add_destinations(forward1)
        
        completiontrans2 = CompletionTrans() .set_name("completiontrans2")
        completiontrans2 .add_sources(forward1) .add_destinations(statenode1)
        
        timertrans2 = TimerTrans(0.5) .set_name("timertrans2")
        timertrans2 .add_sources(statenode1) .add_destinations(check_start2)
        
        successtrans3 = SuccessTrans() .set_name("successtrans3")
        successtrans3 .add_sources(check_start2) .add_destinations(print3)
        
        nulltrans3 = NullTrans() .set_name("nulltrans3")
        nulltrans3 .add_sources(print3) .add_destinations(go_side)
        
        failuretrans4 = FailureTrans() .set_name("failuretrans4")
        failuretrans4 .add_sources(check_start2) .add_destinations(parentpilotevent1)
        
        pilottrans1 = PilotTrans() .set_name("pilottrans1")
        pilottrans1 .add_sources(go_side) .add_destinations(go_side_pilot)
        
        failuretrans5 = FailureTrans() .set_name("failuretrans5")
        failuretrans5 .add_sources(go_side) .add_destinations(parentfails1)
        
        completiontrans3 = CompletionTrans() .set_name("completiontrans3")
        completiontrans3 .add_sources(go_side) .add_destinations(reportposition1)
        
        timertrans3 = TimerTrans(0.75) .set_name("timertrans3")
        timertrans3 .add_sources(reportposition1) .add_destinations(reportposition2)
        
        nulltrans4 = NullTrans() .set_name("nulltrans4")
        nulltrans4 .add_sources(reportposition2) .add_destinations(turn_to_cube1)
        
        completiontrans4 = CompletionTrans() .set_name("completiontrans4")
        completiontrans4 .add_sources(turn_to_cube1) .add_destinations(reportposition3)
        
        timertrans4 = TimerTrans(0.75) .set_name("timertrans4")
        timertrans4 .add_sources(reportposition3) .add_destinations(reportposition4)
        
        nulltrans5 = NullTrans() .set_name("nulltrans5")
        nulltrans5 .add_sources(reportposition4) .add_destinations(approach)
        
        failuretrans6 = FailureTrans() .set_name("failuretrans6")
        failuretrans6 .add_sources(turn_to_cube1) .add_destinations(forward2)
        
        completiontrans5 = CompletionTrans() .set_name("completiontrans5")
        completiontrans5 .add_sources(forward2) .add_destinations(statenode2)
        
        timertrans5 = TimerTrans(1) .set_name("timertrans5")
        timertrans5 .add_sources(statenode2) .add_destinations(turn_to_cube2)
        
        completiontrans6 = CompletionTrans() .set_name("completiontrans6")
        completiontrans6 .add_sources(approach) .add_destinations(reportposition5)
        
        timertrans6 = TimerTrans(0.75) .set_name("timertrans6")
        timertrans6 .add_sources(reportposition5) .add_destinations(reportposition6)
        
        nulltrans6 = NullTrans() .set_name("nulltrans6")
        nulltrans6 .add_sources(reportposition6) .add_destinations(turntocube1)
        
        completiontrans7 = CompletionTrans() .set_name("completiontrans7")
        completiontrans7 .add_sources(turntocube1) .add_destinations(forwardtocube1)
        
        completiontrans8 = CompletionTrans() .set_name("completiontrans8")
        completiontrans8 .add_sources(forwardtocube1) .add_destinations(success)
        
        failuretrans7 = FailureTrans() .set_name("failuretrans7")
        failuretrans7 .add_sources(turn_to_cube2) .add_destinations(print4)
        
        nulltrans7 = NullTrans() .set_name("nulltrans7")
        nulltrans7 .add_sources(print4) .add_destinations(parentfails2)
        
        completiontrans9 = CompletionTrans() .set_name("completiontrans9")
        completiontrans9 .add_sources(turn_to_cube2) .add_destinations(forwardtocube2)
        
        completiontrans10 = CompletionTrans() .set_name("completiontrans10")
        completiontrans10 .add_sources(forwardtocube2) .add_destinations(turn_to_cube3)
        
        completiontrans11 = CompletionTrans() .set_name("completiontrans11")
        completiontrans11 .add_sources(turn_to_cube3) .add_destinations(forwardtocube3)
        
        completiontrans12 = CompletionTrans() .set_name("completiontrans12")
        completiontrans12 .add_sources(forwardtocube3) .add_destinations(success)
        
        return self

class SetCarrying(StateNode):
    def __init__(self,objparam=None):
        self.objparam = objparam
        self.object = None
        super().__init__()
        
    def start(self, event=None):
        if self.objparam is not None:
            self.object = self.objparam
        elif self.object is None:
            self.object = self.parent.object
        if isinstance(self.object, LightCube):
            self.wmobject = self.robot.world.world_map.objects[self.object]
        elif isinstance(self.object, LightCubeObj):
            self.wmobject = self.object
            self.object = self.object.sdk_obj
        else:
            raise ValueError(self.object)
        self.robot.carrying = self.wmobject
        self.wmobject.update_from_sdk = False
        self.wmobject.pose_confidence = +1
        super().start(event)
        self.post_completion()

class SetNotCarrying(StateNode):
    def start(self,event=None):
        self.robot.carrying = None
        self.parent.object = None
        super().start(event)
        self.post_completion()

class PickUpCube(StateNode):
    """Pick up a cube using our own dock and verify routines.
    Set self.object to indicate the cube to be picked up."""
    
    class VerifyPickup(StateNode):
        def probe_column(self, im, col, row_start, row_end):
            """
            Probe one column of the image, looking for the top horizontal
            black bar of the cube marker.  This bar should be 23-32 pixels
            thick.  Use adaptive thresholding by sorting the pixels and
            finding the darkest ones to set the black threshold.
            """
            pixels = [float(im[r,col,0]) for r in range(row_start,row_end)]
            #print('Column ',col,':',sep='')
            #[print('%4d' % i,end='') for i in pixels]
            pixels.sort()
            npix = len(pixels)
            bindex = 1
            bsum = pixels[0]
            bmax = pixels[0]
            bcnt = 1
            windex = npix-2
            wsum = pixels[npix-1]
            wmin = pixels[npix-1]
            wcnt = 1
            while bindex < windex:
                if abs(bmax-pixels[bindex]) < abs(wmin-pixels[windex]):
                    i = bindex
                    bindex += 1
                else:
                    i = windex
                    windex -= 1
                bmean = bsum / bcnt
                wmean = wsum / wcnt
                val = pixels[i]
                if abs(val-bmean) < abs(val-wmean):
                    bsum += val
                    bcnt += 1
                    bmax = max(bmax,val)
                else:
                    wsum += val
                    wcnt +=1
                    wmin = min(wmin,val)
            black_thresh = bmax
            index = row_start
            nrows = im.shape[0]
            black_run_length = 0
            # initial white run
            while index < nrows and im[index,col,0] > black_thresh:
                index += 1
            if index == nrows: return -1
            while index < nrows and im[index,col,0] <= black_thresh:
                black_run_length += 1
                index +=1
            if index >= nrows-5:
                retval = -1
            else:
                retval = black_run_length
            print('  col=%3d wmin=%5.1f wmean=%5.1f bmean=%5.1f black_thresh=%5.1f run_length=%d' %
                  (col, wmin, wmean, bmean, black_thresh, black_run_length))
            return retval

        def start(self,event=None):
            super().start(event)
            im = np.array(self.robot.world.latest_image.raw_image)
            min_length = 20
            max_length = 32
            bad_runs = 0
            print('Verifying pickup.  hangle=%4.1f deg.  langle=%4.1f deg.  lheight=%4.1f mm' %
                  (self.robot.head_angle.degrees, self.robot.lift_angle.degrees,
                   self.robot.lift_height.distance_mm))
            for col in range(100,220,20):
                run_length = self.probe_column(im, col, 0, 100)
                if run_length < min_length or run_length > max_length:
                    bad_runs += 1
            print('  Number of bad_runs:', bad_runs)
            if bad_runs < 2:
                self.post_success()
            else:
                self.post_failure()                

    # PickUpCube methods

    def __init__(self, cube=None):
        self.cube = cube
        super().__init__()

    def picked_up_handler(self):
        print("PickUpCube aborting because robot was picked up.")
        self.post_failure()
        self.stop()

    def start(self, event=None):
        if isinstance(self.cube, LightCube):
            self.object = self.cube
            self.wmobject = self.robot.world.world_map.objects[self.object]
        elif isinstance(self.cube, LightCubeObj):
            self.wmobject = self.cube
            self.object = self.cube.sdk_obj
        elif isinstance(self.object, LightCube):
            self.wmobject = self.robot.world.world_map.objects[self.object]
        elif isinstance(self.object, LightCubeObj):
            self.wmobject = self.object
            self.object = self.object.sdk_obj
        else:
            raise ValueError(self.object)
        self.children['goto_cube'].object = self.object
        print('Picking up',self.wmobject)
        super().start(event)

    def setup(self):
        """
            goto_cube: GoToCube()
            goto_cube =PILOT=> goto_cube_pilot: ParentPilotEvent() =N=> ParentFails()
            goto_cube =F=> ParentFails()
            goto_cube =C=> StopAllMotors() # clear head and lift tracks
              =C=> {raise_lift, raise_head}
    
            #raise_lift: SetLiftHeight(0.7)
            #raise_head: SetHeadAngle(28)
            raise_lift: SetLiftHeight(0.4)
            raise_head: SetHeadAngle(5) =C=> raise_head2: SetHeadAngle(0)
    
            {raise_lift, raise_head2} =C=> verify
    
            verify: self.VerifyPickup()
            verify =S=> satisfied
            verify =F=> StateNode() =T(0.5)=> verify2
            verify2: self.VerifyPickup()
            verify2 =S=> satisfied
            verify2 =F=> StateNode() =T(0.5)=> verify3
            verify3: self.VerifyPickup()
            verify3 =S=> satisfied
            verify3 =F=> frustrated
    
            satisfied: AnimationTriggerNode(trigger=cozmo.anim.Triggers.ReactToBlockPickupSuccess,
                                            ignore_body_track=True,
                                            ignore_head_track=True,
                                            ignore_lift_track=True)
            satisfied =C=> {final_raise, drop_head}
    
            final_raise: SetLiftHeight(1.0)
            drop_head: SetHeadAngle(0)
            {final_raise, drop_head} =C=> SetCarrying() =N=> ParentCompletes()
    
            frustrated: StateNode() =N=> AnimationTriggerNode(trigger=cozmo.anim.Triggers.FrustratedByFailure,
                                             ignore_body_track=True,
                                             ignore_head_track=True,
                                             ignore_lift_track=True) =C=>
            missed_cube: SetNotCarrying() =C=> Forward(-5) =C=> {drop_lift, drop_head_low}
    
            drop_lift: SetLiftHeight(0) =C=> backupmore: Forward(-5)
            drop_head_low: SetHeadAngle(-20)
            {backupmore, drop_head_low} =C=> ParentFails()
    
        """
        
        # Code generated by genfsm on Thu May 24 00:39:49 2018:
        
        goto_cube = GoToCube() .set_name("goto_cube") .set_parent(self)
        goto_cube_pilot = ParentPilotEvent() .set_name("goto_cube_pilot") .set_parent(self)
        parentfails3 = ParentFails() .set_name("parentfails3") .set_parent(self)
        parentfails4 = ParentFails() .set_name("parentfails4") .set_parent(self)
        stopallmotors1 = StopAllMotors() .set_name("stopallmotors1") .set_parent(self)
        raise_lift = SetLiftHeight(0.4) .set_name("raise_lift") .set_parent(self)
        raise_head = SetHeadAngle(5) .set_name("raise_head") .set_parent(self)
        raise_head2 = SetHeadAngle(0) .set_name("raise_head2") .set_parent(self)
        verify = self.VerifyPickup() .set_name("verify") .set_parent(self)
        statenode3 = StateNode() .set_name("statenode3") .set_parent(self)
        verify2 = self.VerifyPickup() .set_name("verify2") .set_parent(self)
        statenode4 = StateNode() .set_name("statenode4") .set_parent(self)
        verify3 = self.VerifyPickup() .set_name("verify3") .set_parent(self)
        satisfied = AnimationTriggerNode(trigger=cozmo.anim.Triggers.ReactToBlockPickupSuccess,
                                        ignore_body_track=True,
                                        ignore_head_track=True,
                                        ignore_lift_track=True) .set_name("satisfied") .set_parent(self)
        final_raise = SetLiftHeight(1.0) .set_name("final_raise") .set_parent(self)
        drop_head = SetHeadAngle(0) .set_name("drop_head") .set_parent(self)
        setcarrying1 = SetCarrying() .set_name("setcarrying1") .set_parent(self)
        parentcompletes1 = ParentCompletes() .set_name("parentcompletes1") .set_parent(self)
        frustrated = StateNode() .set_name("frustrated") .set_parent(self)
        animationtriggernode1 = AnimationTriggerNode(trigger=cozmo.anim.Triggers.FrustratedByFailure,
                                         ignore_body_track=True,
                                         ignore_head_track=True,
                                         ignore_lift_track=True) .set_name("animationtriggernode1") .set_parent(self)
        missed_cube = SetNotCarrying() .set_name("missed_cube") .set_parent(self)
        forward3 = Forward(-5) .set_name("forward3") .set_parent(self)
        drop_lift = SetLiftHeight(0) .set_name("drop_lift") .set_parent(self)
        backupmore = Forward(-5) .set_name("backupmore") .set_parent(self)
        drop_head_low = SetHeadAngle(-20) .set_name("drop_head_low") .set_parent(self)
        parentfails5 = ParentFails() .set_name("parentfails5") .set_parent(self)
        
        pilottrans2 = PilotTrans() .set_name("pilottrans2")
        pilottrans2 .add_sources(goto_cube) .add_destinations(goto_cube_pilot)
        
        nulltrans8 = NullTrans() .set_name("nulltrans8")
        nulltrans8 .add_sources(goto_cube_pilot) .add_destinations(parentfails3)
        
        failuretrans8 = FailureTrans() .set_name("failuretrans8")
        failuretrans8 .add_sources(goto_cube) .add_destinations(parentfails4)
        
        completiontrans13 = CompletionTrans() .set_name("completiontrans13")
        completiontrans13 .add_sources(goto_cube) .add_destinations(stopallmotors1)
        
        completiontrans14 = CompletionTrans() .set_name("completiontrans14")
        completiontrans14 .add_sources(stopallmotors1) .add_destinations(raise_lift,raise_head)
        
        completiontrans15 = CompletionTrans() .set_name("completiontrans15")
        completiontrans15 .add_sources(raise_head) .add_destinations(raise_head2)
        
        completiontrans16 = CompletionTrans() .set_name("completiontrans16")
        completiontrans16 .add_sources(raise_lift,raise_head2) .add_destinations(verify)
        
        successtrans4 = SuccessTrans() .set_name("successtrans4")
        successtrans4 .add_sources(verify) .add_destinations(satisfied)
        
        failuretrans9 = FailureTrans() .set_name("failuretrans9")
        failuretrans9 .add_sources(verify) .add_destinations(statenode3)
        
        timertrans7 = TimerTrans(0.5) .set_name("timertrans7")
        timertrans7 .add_sources(statenode3) .add_destinations(verify2)
        
        successtrans5 = SuccessTrans() .set_name("successtrans5")
        successtrans5 .add_sources(verify2) .add_destinations(satisfied)
        
        failuretrans10 = FailureTrans() .set_name("failuretrans10")
        failuretrans10 .add_sources(verify2) .add_destinations(statenode4)
        
        timertrans8 = TimerTrans(0.5) .set_name("timertrans8")
        timertrans8 .add_sources(statenode4) .add_destinations(verify3)
        
        successtrans6 = SuccessTrans() .set_name("successtrans6")
        successtrans6 .add_sources(verify3) .add_destinations(satisfied)
        
        failuretrans11 = FailureTrans() .set_name("failuretrans11")
        failuretrans11 .add_sources(verify3) .add_destinations(frustrated)
        
        completiontrans17 = CompletionTrans() .set_name("completiontrans17")
        completiontrans17 .add_sources(satisfied) .add_destinations(final_raise,drop_head)
        
        completiontrans18 = CompletionTrans() .set_name("completiontrans18")
        completiontrans18 .add_sources(final_raise,drop_head) .add_destinations(setcarrying1)
        
        nulltrans9 = NullTrans() .set_name("nulltrans9")
        nulltrans9 .add_sources(setcarrying1) .add_destinations(parentcompletes1)
        
        nulltrans10 = NullTrans() .set_name("nulltrans10")
        nulltrans10 .add_sources(frustrated) .add_destinations(animationtriggernode1)
        
        completiontrans19 = CompletionTrans() .set_name("completiontrans19")
        completiontrans19 .add_sources(animationtriggernode1) .add_destinations(missed_cube)
        
        completiontrans20 = CompletionTrans() .set_name("completiontrans20")
        completiontrans20 .add_sources(missed_cube) .add_destinations(forward3)
        
        completiontrans21 = CompletionTrans() .set_name("completiontrans21")
        completiontrans21 .add_sources(forward3) .add_destinations(drop_lift,drop_head_low)
        
        completiontrans22 = CompletionTrans() .set_name("completiontrans22")
        completiontrans22 .add_sources(drop_lift) .add_destinations(backupmore)
        
        completiontrans23 = CompletionTrans() .set_name("completiontrans23")
        completiontrans23 .add_sources(backupmore,drop_head_low) .add_destinations(parentfails5)
        
        return self

class DropObject(StateNode):
    def __init__(self):
        super().__init__()

    def setup(self):
        """
            SetLiftHeight(0) =C=> SetNotCarrying() =N=> {backup, lookdown}
    
            backup: Forward(-10)
            lookdown: SetHeadAngle(-20)
    
            {backup, lookdown} =C=> ParentCompletes()
        """
        
        # Code generated by genfsm on Thu May 24 00:39:49 2018:
        
        setliftheight1 = SetLiftHeight(0) .set_name("setliftheight1") .set_parent(self)
        setnotcarrying1 = SetNotCarrying() .set_name("setnotcarrying1") .set_parent(self)
        backup = Forward(-10) .set_name("backup") .set_parent(self)
        lookdown = SetHeadAngle(-20) .set_name("lookdown") .set_parent(self)
        parentcompletes2 = ParentCompletes() .set_name("parentcompletes2") .set_parent(self)
        
        completiontrans24 = CompletionTrans() .set_name("completiontrans24")
        completiontrans24 .add_sources(setliftheight1) .add_destinations(setnotcarrying1)
        
        nulltrans11 = NullTrans() .set_name("nulltrans11")
        nulltrans11 .add_sources(setnotcarrying1) .add_destinations(backup,lookdown)
        
        completiontrans25 = CompletionTrans() .set_name("completiontrans25")
        completiontrans25 .add_sources(backup,lookdown) .add_destinations(parentcompletes2)
        
        return self


class PickUpCubeForeign(StateNode):

    # *** THIS IS OLD CODE AND NEEDS TO BE UPDATED ***

    def __init__(self, cube_id=None):
        self.object_id = cube_id
        super().__init__()

    def start(self, event=None):
        # self.object will be set up by the parent of this node
        self.object = self.robot.world.light_cubes[self.object_id]
        self.foreign_cube_id = 'LightCubeForeignObj-'+str(self.object_id)
        super().start(event)

    def pick_side(self, dist, use_world_map):
        "NOTE: This code is only correct for upright cubes"
        cube = self.foreign_cube_id
        wobj = self.robot.world.world_map.objects[cube]
        x = wobj.x
        y = wobj.y
        ang = wobj.theta
        rx = self.robot.world.particle_filter.pose[0]
        ry = self.robot.world.particle_filter.pose[1]

        side1 = (x + cos(ang) * dist, y + sin(ang) * dist, ang + pi)
        side2 = (x - cos(ang) * dist, y - sin(ang) * dist, ang)
        side3 = (x + sin(ang) * dist, y - cos(ang) * dist, ang + pi/2)
        side4 = (x - sin(ang) * dist, y + cos(ang) * dist, ang - pi/2)
        sides = [side1, side2, side3, side4]
        sorted_sides = sorted(sides, key=lambda pt: (pt[0]-rx)**2 + (pt[1]-ry)**2)
        return sorted_sides[0]

    class GoToSide(WallPilotToPose):
        def __init__(self):
            super().__init__(None)

        def start(self, event=None):
            cube = self.parent.foreign_cube_id
            print('Selected cube',self.robot.world.world_map.objects[cube])
            (x, y, theta) = self.parent.pick_side(200, True)
            self.target_pose = Pose(x, y, self.robot.pose.position.z,
                                    angle_z=Angle(radians = wrap_angle(theta)))
            print('pickup.GoToSide: traveling to (%.1f, %.1f) @ %.1f deg.' %
                  (self.target_pose.position.x, self.target_pose.position.y,
                   self.target_pose.rotation.angle_z.degrees))
            super().start(event)

    class Pick(PickUpCube):
        def __init__(self):
            super().__init__(None)

        def start(self, event=None):
            self.object = self.parent.object
            super().start(event)

    def setup(self):
        """
            goto_cube: self.GoToSide() =C=> one
    
            one: self.Pick() =C=> end
            end: Say("Done") =C=> ParentCompletes()
        """
        
        # Code generated by genfsm on Thu May 24 00:39:49 2018:
        
        goto_cube = self.GoToSide() .set_name("goto_cube") .set_parent(self)
        one = self.Pick() .set_name("one") .set_parent(self)
        end = Say("Done") .set_name("end") .set_parent(self)
        parentcompletes3 = ParentCompletes() .set_name("parentcompletes3") .set_parent(self)
        
        completiontrans26 = CompletionTrans() .set_name("completiontrans26")
        completiontrans26 .add_sources(goto_cube) .add_destinations(one)
        
        completiontrans27 = CompletionTrans() .set_name("completiontrans27")
        completiontrans27 .add_sources(one) .add_destinations(end)
        
        completiontrans28 = CompletionTrans() .set_name("completiontrans28")
        completiontrans28 .add_sources(end) .add_destinations(parentcompletes3)
        
        return self
