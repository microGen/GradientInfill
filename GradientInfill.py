# GradientInfill
"""
Gradient Infill for 3D prints.

License: MIT
Author: Stefan Hermann - CNC Kitchen
Version: 1.0

Modification : 19/01/2020  -> Transform into a Cura Postprocessing PlugIn
"""

from ..Script import Script
from UM.Logger import Logger
from UM.Application import Application
import re #To perform the search and replace.
from cura.Settings.ExtruderManager import ExtruderManager
from collections import namedtuple
from enum import Enum
from typing import List, Tuple
from UM.Message import Message
from UM.i18n import i18nCatalog
catalog = i18nCatalog("cura")

__version__ = '1.0'


Point2D = namedtuple('Point2D', 'x y')
Segment = namedtuple('Segment', 'point1 point2')


# MAX_FLOW = 350.0  # maximum extrusion flow
# MIN_FLOW = 50.0  # minimum extrusion flow
# GRADIENT_THICKNESS = 6.0  # thickness of the gradient (max to min) in mm
# GRADIENT_DISCRETIZATION = 4.0  # only applicable for linear infills; number of segments within the
# gradient(segmentLength=gradientThickness / gradientDiscretization); use sensible values to not overload the printer


class Infill(Enum):
    """Enum for infill type."""

    SMALL_SEGMENTS = 1  # infill with small segments like honeycomb or gyroid
    LINEAR = 2  # linear infill like rectilinear or triangles

class Section(Enum):
    """Enum for section type."""

    NOTHING = 0
    INNER_WALL = 1
    INFILL = 2


def dist(segment: Segment, point: Point2D) -> float:
    """Calculate the distance from a point to a line with finite length.

    Args:
        segment (Segment): line used for distance calculation
        point (Point2D): point used for distance calculation

    Returns:
        float: distance between ``segment`` and ``point``
    """
    px = segment.point2.x - segment.point1.x
    py = segment.point2.y - segment.point1.y
    norm = px * px + py * py
    u = ((point.x - segment.point1.x) * px + (point.y - segment.point1.y) * py) / float(norm)
    if u > 1:
        u = 1
    elif u < 0:
        u = 0
    x = segment.point1.x + u * px
    y = segment.point1.y + u * py
    dx = x - point.x
    dy = y - point.y

    return (dx * dx + dy * dy) ** 0.5


def get_points_distance(point1: Point2D, point2: Point2D) -> float:
    """Calculate the euclidean distance between two points.

    Args:
        point1 (Point2D): first point
        point2 (Point2D): second point

    Returns:
        float: euclidean distance between the points
    """
    return ((point1.x - point2.x) ** 2 + (point1.y - point2.y) ** 2) ** 0.5


def min_distance_from_segment(segment: Segment, segments: List[Segment]) -> float:
    """Calculate the minimum distance from the midpoint of ``segment`` to the nearest segment in ``segments``.

    Args:
        segment (Segment): segment to use for midpoint calculation
        segments (List[Segment]): segments list

    Returns:
        float: the smallest distance from the midpoint of ``segment`` to the nearest segment in the list
    """
    middlePoint = Point2D((segment.point1.x + segment.point2.x) / 2, (segment.point1.y + segment.point2.y) / 2)

    return min(dist(s, middlePoint) for s in segments)


def getXY(currentLine: str) -> Point2D:
    """Create a ``Point2D`` object from a gcode line.

    Args:
        currentLine (str): gcode line

    Raises:
        SyntaxError: when the regular expressions cannot find the relevant coordinates in the gcode

    Returns:
        Point2D: the parsed coordinates
    """
    searchX = re.search(r"X(\d*\.?\d*)", currentLine)
    searchY = re.search(r"Y(\d*\.?\d*)", currentLine)
    if searchX and searchY:
        elementX = searchX.group(1)
        elementY = searchY.group(1)
    else:
        raise SyntaxError('Gcode file parsing error for line {currentLine}')

    return Point2D(float(elementX), float(elementY))


def mapRange(a: Tuple[float, float], b: Tuple[float, float], s: float) -> float:
    """Calculate a multiplier for the extrusion value from the distance to the perimeter.

    Args:
        a (Tuple[float, float]): a tuple containing:
            - a1 (float): the minimum distance to the perimeter (always zero at the moment)
            - a2 (float): the maximum distance to the perimeter where the interpolation is performed
        b (Tuple[float, float]): a tuple containing:
            - b1 (float): the maximum flow as a fraction
            - b2 (float): the minimum flow as a fraction
        s (float): the euclidean distance from the middle of a segment to the nearest perimeter

    Returns:
        float: a multiplier for the modified extrusion value
    """
    (a1, a2), (b1, b2) = a, b

    return b1 + ((s - a1) * (b2 - b1) / (a2 - a1))


def get_extrusion_command(x: float, y: float, extrusion: float) -> str:
    """Format a gcode string from the X, Y coordinates and extrusion value.

    Args:
        x (float): X coordinate
        y (float): Y coordinate
        extrusion (float): Extrusion value

    Returns:
        str: Gcode line
    """
    return "G1 X{} Y{} E{}".format(round(x, 3), round(y, 3), round(extrusion, 5))


def is_begin_layer_line(line: str) -> bool:
    """Check if current line is the start of a layer section.

    Args:
        line (str): Gcode line

    Returns:
        bool: True if the line is the start of a layer section
    """
    return line.startswith(";LAYER:")


def is_begin_inner_wall_line(line: str) -> bool:
    """Check if current line is the start of an inner wall section.

    Args:
        line (str): Gcode line

    Returns:
        bool: True if the line is the start of an inner wall section
    """
    return line.startswith(";TYPE:WALL-INNER")


def is_end_inner_wall_line(line: str) -> bool:
    """Check if current line is the start of an outer wall section.

    Args:
        line (str): Gcode line

    Returns:
        bool: True if the line is the start of an outer wall section
    """
    return line.startswith(";TYPE:WALL-OUTER")


def is_extrusion_line(line: str) -> bool:
    """Check if current line is a standard printing segment.

    Args:
        line (str): Gcode line

    Returns:
        bool: True if the line is a standard printing segment
    """
    return "G1" in line and " X" in line and "Y" in line and "E" in line


def is_begin_infill_segment_line(line: str) -> bool:
    """Check if current line is the start of an infill.

    Args:
        line (str): Gcode line

    Returns:
        bool: True if the line is the start of an infill section
    """
    return line.startswith(";TYPE:FILL")


def mfill_mode(Mode):
    """Definie the type of Infill pattern

       linear infill like rectilinear or triangles = 2
       infill with small segments like gyroid = 1

    Args:
        line (Mode): Infill Pattern

    Returns:
        Int: the Type of infill pattern
    """
    iMode=0
    if Mode == 'grid':
        iMode=2
    if Mode == 'lines':
        iMode=2
    if Mode == 'triangles':
        iMode=2
    if Mode == 'trihexagon':
        iMode=2
    if Mode == 'cubic':
        iMode=2
    if Mode == 'cubicsubdiv':
        iMode=2
    if Mode == 'tetrahedral':
        iMode=2
    if Mode == 'quarter_cubic':
        iMode=2
    if Mode == 'concentric':
        iMode=0
    if Mode == 'zigzag':
        iMode=2
    if Mode == 'cross':
        iMode=0
    if Mode == 'cross_3d':
        iMode=0
    if Mode == 'gyroid':
        iMode=1

    return iMode
        
class GradientInfill(Script):
    def getSettingDataString(self):
        return """{
            "name": "Gradient Infill",
            "key": "GradientInfill",
            "metadata": {},
            "version": 2,
            "settings":
            {
                "gradientthickness":
                {
                    "label": "Gradient Distance",
                    "description": "Distance of the gradient (max to min) in mm",
                    "type": "float",
                    "default_value": 6.0
                },
                "gradientdiscretization":
                {
                    "label": "Gradient Discretization",
                    "description": "Only applicable for linear infills; number of segments within the gradient(segmentLength=gradientThickness / gradientDiscretization); use sensible values to not overload",
                    "type": "int",
                    "default_value": 4,
                    "minimum_value": 2,
                    "minimum_value_warning": 3
                },
                "maxflow":
                {
                    "label": "Max flow",
                    "description": "maximum extrusion flow",
                    "type": "float",
                    "default_value": 350.0,
                    "minimum_value": 100.0
                },
                "minflow":
                {
                    "label": "Min flow",
                    "description": "minimum extrusion flow",
                    "type": "float",
                    "default_value": 50.0,
                    "minimum_value": 0.0,
                    "maximum_value": 100.0,
                    "minimum_value_warning": 10.0,
                    "maximum_value_warning": 90.0
                },
                "extruder_nb":
                {
                    "label": "Extruder Id",
                    "description": "Define extruder Id in case of multi extruders",
                    "unit": "",
                    "type": "int",
                    "default_value": 1
                }
            }
        }"""


##  Performs a search-and-replace on all g-code.
#
#   Due to technical limitations, the search can't cross the border between
#   layers.

    def execute(self, data):

        gradient_discretization = float(self.getSettingValueByKey("gradientdiscretization"))
        max_flow= float(self.getSettingValueByKey("maxflow"))
        min_flow= float(self.getSettingValueByKey("minflow"))
        gradient_thickness= float(self.getSettingValueByKey("gradientthickness"))
        extruder_id  = self.getSettingValueByKey("extruder_nb")
        extruder_id = extruder_id -1
        
        #   machine_extruder_count
        extruder_count=Application.getInstance().getGlobalContainerStack().getProperty("machine_extruder_count", "value")
        extruder_count = extruder_count-1
        if extruder_id>extruder_count :
            extruder_id=extruder_count

        extrud = list(Application.getInstance().getGlobalContainerStack().extruders.values())

        infillpattern = extrud[extruder_id].getProperty("infill_pattern", "value")
        relativeextrusion = extrud[extruder_id].getProperty("relative_extrusion", "value")
        if relativeextrusion == False:
            Logger.log('d', 'Gcode must be generate in relative extrusion')
            # Message('Gcode must be generate in relative extrusion', title = catalog.i18nc("@info:title", "Post Processing")).show()
            # raise SyntaxError('Gcode must be generate in relative extrusion')
        
        """Parse Gcode and modify infill portions with an extrusion width gradient."""
        currentSection = Section.NOTHING
        lastPosition = Point2D(-10000, -10000)
        gradientDiscretizationLength = gradient_thickness / gradient_discretization

        infill_type=mfill_mode(infillpattern)

        Logger.log('d',  "DradientFill Param : " + str(gradientDiscretizationLength) + "/" + str(max_flow) + "/" + str(min_flow) + "/" + str(gradient_discretization)+ "/" + str(gradient_thickness) )
        Logger.log('d',  "Pattern Param : " + infillpattern + "/" + str(infill_type) )

        for layer in data:
            layer_index = data.index(layer)
            lines = layer.split("\n")
            for currentLine in lines:
                line_index = lines.index(currentLine)
                
                if is_begin_layer_line(currentLine):
                    perimeterSegments = []
                if is_begin_inner_wall_line(currentLine):
                    currentSection = Section.INNER_WALL

                if currentSection == Section.INNER_WALL and is_extrusion_line(currentLine):
                    perimeterSegments.append(Segment(getXY(currentLine), lastPosition))

                if is_end_inner_wall_line(currentLine):
                    currentSection = Section.NOTHING

                if is_begin_infill_segment_line(currentLine):
                    currentSection = Section.INFILL
                    # outputFile.write(currentLine)
                    continue

                if currentSection == Section.INFILL:
                    if "F" in currentLine and "G1" in currentLine:
                        searchSpeed = re.search(r"F(\d*\.?\d*)", currentLine)
                        if searchSpeed:
                            new_Line="G1 F{}\n".format(searchSpeed.group(1))
                        else:
                            # raise SyntaxError('Gcode file parsing error for line {currentLine}')
                            Logger.log('d', 'Gcode file parsing error for line : ' + currentLine )
                    
                    if "E" in currentLine and "G1" in currentLine and " X" in currentLine and "Y" in currentLine:
                        currentPosition = getXY(currentLine)
                        splitLine = currentLine.split(" ")

                        # if infill_type == Infill.LINEAR:  
                        if infill_type == 2:
                            # find extrusion length
                            for element in splitLine:
                                if "E" in element:
                                    extrusionLength = float(element[1:])

                            segmentLength = get_points_distance(lastPosition, currentPosition)
                            segmentSteps = segmentLength / gradientDiscretizationLength
                            extrusionLengthPerSegment = extrusionLength / segmentSteps
                            segmentDirection = Point2D((currentPosition.x - lastPosition.x) / segmentLength * gradientDiscretizationLength,(currentPosition.y - lastPosition.y) / segmentLength * gradientDiscretizationLength)
 
                            if segmentSteps >= 2:
                                # new_Line=new_Line+"; GradientInfill segmentSteps >= 2\n"
                                for step in range(int(segmentSteps)):
                                    segmentEnd = Point2D(lastPosition.x + segmentDirection.x, lastPosition.y + segmentDirection.y)
                                    shortestDistance = min_distance_from_segment(Segment(lastPosition, segmentEnd), perimeterSegments)
                                    if shortestDistance < gradient_thickness:
                                        segmentExtrusion = extrusionLengthPerSegment * mapRange((0, gradient_thickness), (max_flow / 100, min_flow / 100), shortestDistance)
                                    else:
                                        segmentExtrusion = extrusionLengthPerSegment * min_flow / 100

                                    new_Line=new_Line + get_extrusion_command(segmentEnd.x, segmentEnd.y, segmentExtrusion) + "\n"
                                    lastPosition = segmentEnd

                                # MissingSegment
                                segmentLengthRatio = get_points_distance(lastPosition, currentPosition) / segmentLength
                                new_Line=new_Line+get_extrusion_command(currentPosition.x,currentPosition.y,segmentLengthRatio * extrusionLength * max_flow / 100)
                                
                                lines[line_index] = new_Line
                                
                            else :
                                outPutLine = ""
                                for element in splitLine:
                                    if "E" in element:
                                        outPutLine = outPutLine + "E" + str(round(extrusionLength * max_flow / 100, 5))
                                    else:
                                        outPutLine = outPutLine + element + " "
                                outPutLine = outPutLine # + "\n"
                                lines[line_index] = outPutLine
                                
                            # writtenToFile = 1
                            
                        # gyroid or honeycomb
                        # if infill_type == Infill.SMALL_SEGMENTS:
                        if infill_type == 1:
                            shortestDistance = min_distance_from_segment(Segment(lastPosition, currentPosition), perimeterSegments)

                            outPutLine = new_Line
                            if shortestDistance < gradient_thickness:
                                for element in splitLine:
                                    if "E" in element:
                                        newE = float(element[1:]) * mapRange((0, gradient_thickness), (max_flow / 100, min_flow / 100), shortestDistance)
                                        outPutLine = outPutLine + "E" + str(round(newE, 5))
                                    else:
                                        outPutLine = outPutLine + element + " "

                                outPutLine = outPutLine + "\n"
                                lines[line_index] = outPutLine

                                # writtenToFile = 1
                    if ";" in currentLine:
                        currentSection = Section.NOTHING

                # line with move
                if " X" in currentLine and " Y" in currentLine and ("G1" in currentLine or "G0" in currentLine):
                    lastPosition = getXY(currentLine)

                # write uneditedLine
                # if writtenToFile == 0:
                    # outputFile.write(currentLine)
                    

            final_lines = "\n".join(lines)
            data[layer_index] = final_lines
        return data
    
