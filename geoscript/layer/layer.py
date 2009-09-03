"""
layer module -- Provides dataset access and manipulation.
"""
import sys

from java import io
from java import net
from geoscript import geom
from geoscript.feature import Feature, FeatureType
from org.geotools.data import DefaultQuery, Query, Transaction
from org.geotools.feature import FeatureCollections
from org.geotools.filter.text.cql2 import CQL
from org.opengis.filter import Filter

class Layer:

  def __init__(self,fs=None):
    if self.__class__ == Layer:
      import memory
      mem = memory.MemoryLayer('layer',[('geom',geom.Geometry)])
      self.fs = mem.fs
      self.name = mem.name
      self.ftype = mem.ftype
    else:
      if not fs:
        raise Exception('Layer requires a feature source.')

      self.fs = fs
      self.name = fs.name.localPart
      self.ftype = FeatureType(ft=fs.schema) 

  def count(self):
    """
    The number of features in the layer.

    >>> l = Layer()
    >>> l.count()
    0
    >>> from geoscript import geom
    >>> l.add([geom.point(1,2)])
    >>> l.count()
    1
    """

    return self.fs.getCount(Query.ALL)

  def bounds(self):
    """
    The bounds of the layer.

    >>> l = Layer()
    >>> from geoscript import geom 
    >>> l.add([geom.point(1.0, 2.0)])
    >>> l.add([geom.point(3.0, 4.0)])

    >>> l.bounds()
    ReferencedEnvelope[1.0 : 3.0, 2.0 : 4.0]
    """

    return self.fs.bounds

  def features(self, filter=None):
    """
    Iterates over features in the layer.

    >>> l = Layer()
    >>> from geoscript import geom
    >>> l.add([geom.point(1,2)])
    >>> l.add([geom.point(3,4)])
    >>> [ str(f.geom()) for f in l.features() ]
    ['POINT (1 2)', 'POINT (3 4)']

    This method takes an option filter, specified as CQL:

    >>> [ str(f.geom()) for f in l.features('INTERSECT(geom,POINT(3 4))') ]
    ['POINT (3 4)']

    """

    f = None 
    if filter:
       f = CQL.toFilter(filter)
    else:
       f = Filter.INCLUDE

    q = DefaultQuery(self.name,f)
    r = self.fs.dataStore.getFeatureReader(q,Transaction.AUTO_COMMIT)
    while r.hasNext():
      yield Feature(ftype=self.ftype, f=r.next())

    r.close()

  def add(self, o):
    """
    Adds a feature to the layer.

    >>> l = Layer() 
    >>> l.count()
    0
    >>> from geoscript import geom
    >>> l.add([geom.point(1,2)])
    >>> l.count()
    1
    """
    if isinstance(o,Feature):
      f = o
      if not f.ftype:
        f.ftype = self.ftype
    elif isinstance(o, list):
      f = self.ftype.feature(o)
      
    fc = FeatureCollections.newCollection() 
    fc.add(f.f)
    self.fs.addFeatures(fc)

  def toGML(self,out=sys.stdout):
    try:
      from net.opengis.wfs import WfsFactory
      from org.geotools.wfs.v1_1 import WFS, WFSConfiguration
      from org.geotools.xml import Encoder
    except ImportError:
      raise Exception('toGML() not available, GML libraries not on classpath.') 

    features = self.fs.features
    fc = WfsFactory.eINSTANCE.createFeatureCollectionType()
    fc.feature.add(features)

    e = Encoder(WFSConfiguration())        
    uri = self.fs.name.namespaceURI
    prefix = 'gt'
    e.namespaces.declarePrefix(prefix,uri)
    e.indenting = True
    e.encode(fc, WFS.FeatureCollection, out)

  def toJSON(self,out=sys.stdout):
    try:
      from org.geotools.geojson import GeoJSONWriter
    except ImportError:
      raise Exception('toJSON() not available, GeoJSON libraries not on classpath.')
    else:
      features = self.fs.features
      w = GeoJSONWriter() 
      w.write(features,out)