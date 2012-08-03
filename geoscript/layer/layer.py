"""
The :mod:`layer.layer` module provides the classes for data access and manipulation.
"""
import sys, math
from java import io
from java import net
from cursor import Cursor
from geoscript import core, geom, proj, feature
from geoscript.filter import Filter
from geoscript.util.data import readFeatures
from org.geotools.data import FeatureSource, DefaultQuery, Query, Transaction
from org.geotools.factory import CommonFactoryFinder
from org.geotools.feature import FeatureCollection, FeatureCollections
from org.opengis.filter.sort import SortOrder

_filterFactory = CommonFactoryFinder.getFilterFactory(None)

class Layer(object):
  """
  A source of spatial data.
  """

  _id = -1
  """
  Internal counter for generating layer names
  """

  def __init__(self, name=None, workspace=None, fs=None, schema=None):
    if not workspace:
       from geoscript.workspace import Memory
       workspace = Memory()
 
    name = name if name else schema.name if schema else Layer._newname()
       
    if not fs:
       layer = None
       try:
         layer = workspace.get(name)
       except KeyError:
         pass
        
       if not layer:
         if schema:
           layer = workspace.create(schema.name, schema.fields)
         else:
           layer = workspace.create(name)

       fs = layer._source

    self.workspace = workspace
    self.schema = schema or feature.Schema(ft=fs.schema) 
    self._source = fs

    # keep a crs local to allow the native crs to be overriden, or to 
    # provide a crs for layers that don't have one specified
    self._proj = None

  def getformat(self):
    return self.workspace._format(self)

  format = property(getformat)
  """
  A ``str`` identifying the format of the layer.
  """

  def getname(self):
    return self._source.name.localPart

  name = property(getname)
  """
  The name of the layer as a ``str``.
  """

  def getproj(self):
    if self._proj:
      return self._proj
    else:
      crs = self._source.schema.coordinateReferenceSystem
      if crs:
        return proj.Projection(crs)

  def setproj(self, value):
    self._proj = proj.Projection(value) 

  proj = property(getproj, setproj)
  """
  The :class:`Projection <geoscript.proj.Projection>` of the layer. In cases where the projection of a layer is 
  unknown this attribute has the value ``None``.

  >>> import proj
  >>> l = Layer()
  >>> l.proj
  None
  >>> l.proj = proj.Projection('epsg:4326')
  >>> l.proj.id
  EPSG:4326
  """
  
  def getextent(self): 
    """
    The extent of the entire layer as a :class:`Bounds <geoscript.geom.Bounds>`.

    >>> l = Layer()
    >>> from geoscript import geom 
    >>> l.add([geom.Point(1.0, 2.0)])
    >>> l.add([geom.Point(3.0, 4.0)])

    >>> l.extent
    (1.0, 2.0, 3.0, 4.0, EPSG:4326)
    
    """
    return self.bounds()
 
  extent = property(getextent, None)

  def count(self, filter=None):
    """
    The number of features in the layer as an ``int``.

    *filter* is an optional :class:`Filter <geoscript.filter.Filter>` to constrains the counted set of features.

    >>> l = Layer()
    >>> l.count()
    0
    >>> from geoscript import geom
    >>> l.add([geom.Point(1,2)])
    >>> l.add([geom.Point(3,4)])
    >>> l.count() 
    2
    >>> l.count('INTERSECTS(geom,POINT(3 4))')
    1
    """

    f = Filter(filter) if filter else Filter.PASS
    count = self._source.getCount(DefaultQuery(self.name, f._filter))
    if count == -1:
      count = 0
      # calculate manually 
      for f in self.features(filter):
        count += 1

    return count

  def bounds(self, filter=None):
    """
    The :class:`Bounds <geoscript.geom.Bounds>` of the layer.

    *filter* is an optional :class:`Filter <geoscript.filter.Filter>` to constrains the returned bounds.

    >>> l = Layer()
    >>> from geoscript import geom 
    >>> l.add([geom.Point(1.0, 2.0)])
    >>> l.add([geom.Point(3.0, 4.0)])

    >>> l.bounds()
    (1.0, 2.0, 3.0, 4.0, EPSG:4326)

    >>> l.bounds('INTERSECTS(geom,POINT(3 4))')
    (3.0, 4.0, 3.0, 4.0, EPSG:4326)
    """

    f = Filter(filter) if filter else Filter.PASS
    q = DefaultQuery(self.name, f._filter)
    e = self._source.getBounds(q)

    if not e:
      # try through feature collection
      fc = self._source.getFeatures(q)
      e = fc.getBounds()
    if e:
      if e.crs():
        return geom.Bounds(env=e)
      else:
        return geom.Bounds(env=e, prj=self.proj)
    else:
      # calculate manually
      fit = self._source.getFeatures(q).features()
      try:
        bounds = geom.Bounds(prj=self.proj)
        if fit.hasNext(): 
          bounds.init(fit.next().getBounds())
          while fit.hasNext():
            bounds.expland(fit.next().getBounds())
        return bounds
      finally:
        fit.close()


  def features(self, filter=None, transform=None, sort=None):
    """
    Generator over the :class:`Feature <geoscript.feature.Feature>` s of the layer.

    *filter* is a optional :class:`Filter <geoscript.filter.Filter>` to constrain the features iterated over.

    *transform* is an optional function to be executed to transform the features being iterated over. This 
    function takes a single argument which is a :class:`Feature <geoscript.feature.Feature>` and returns a 
    (possibly different) feature.
 
    *sort* is an optional tuple or ``list`` of tuples that defined the order in
    which features are iterated over. The first value of each tuple is the name
    of a field to sort on. The second value is one of the strings 'ASC' or 
    'DESC', representing ascending and decending sort order respectively. 

    >>> l = Layer()
    >>> from geoscript import geom
    >>> l.add([geom.Point(1,2)])
    >>> l.add([geom.Point(3,4)])
    >>> [ str(f.geom) for f in l.features() ]
    ['POINT (1 2)', 'POINT (3 4)']

    >>> [ str(f.geom) for f in l.features('INTERSECTS(geom,POINT(3 4))') ]
    ['POINT (3 4)']

    >>> def tx (f):
    ...    f.geom = geom.Point(2*f.geom.x, 2*f.geom.y)
    >>> [str(f.geom) for f in l.features(transform=tx)]
    ['POINT (2 4)', 'POINT (6 8)']
    """
    c = self.cursor(filter, sort)
    for f in c:
      if transform:
         result  = transform(f)
         if result and isinstance(result, Feature):
           f = result

      yield f

    c.close()

  def cursor(self, filter=None, sort=None):
    """
    Returns a :class:`Cursor <geoscript.layer.cursor.Cursor>` over the features of the layer.

    *filter* is a optional :class:`Filter <geoscript.filter.Filter>` to constrain the features iterated over.

    *sort* is an optional tuple or ``list`` of tuples that defined the order in
    which features are iterated over. The first value of each tuple is the name
    of a field to sort on. The second value is one of the strings 'ASC' or 
    'DESC', representing ascending and decending sort order respectively. 

    >>> l = Layer()
    >>> from geoscript import geom
    >>> l.add([geom.Point(1,2)])
    >>> l.add([geom.Point(3,4)])
    >>> l.add([geom.Point(5,6)])
    >>> l.add([geom.Point(7,8)])
    >>> l.add([geom.Point(9,10)])
    >>> c = l.cursor()
    >>> f = c.next() 
    >>> f.geom
    POINT (1 2)
    >>> f = c.next() 
    >>> f.geom
    POINT (3 4)
    >>> features = c.read(2)
    >>> len(features)
    2
    >>> features[0].geom
    POINT (5 6)
    >>> features[1].geom
    POINT (7 8)
    >>> features = c.read(2)
    >>> len(features)
    1
    >>> features[0].geom
    POINT (9 10)
    >>> c.close()
    """

    f = Filter(filter) if filter else Filter.PASS
    q = DefaultQuery(self.name, f._filter)
    if sort:
      sort = sort if isinstance(sort, list) else [sort]
      sortBy = [] 
      ff = _filterFactory
      for s in sort: 
        s = s if isinstance(s, tuple) else [s, 'ASC']
        sortBy.append(ff.sort(s[0], SortOrder.valueOf(s[1])))
        q.setSortBy(sortBy)
    if self.proj:
      q.coordinateSystem = self.proj._crs

    fcol = self._source.getFeatures(q)
    #r = self._source.dataStore.getFeatureReader(q,Transaction.AUTO_COMMIT)
    return Cursor(fcol, self)

  def delete(self, filter):
    """
    Deletes features from the layer which match the specified constraint.

    *filter* is a :class:`Filter <geoscript.filter.Filter>` that specifies which features are to be deleted.

    >>> l = Layer()
    >>> from geoscript import geom
    >>> l.add([geom.Point(1,2)])
    >>> l.add([geom.Point(3,4)])
    >>> l.count()
    2
    >>> l.delete('INTERSECTS(geom, POINT(3 4))')
    >>> l.count()
    1
    """

    f = Filter(filter) if filter else Filter.FAIL
    self._source.removeFeatures(f._filter)

  def add(self, o):
    """
    Adds a :class:`Feature <geoscript.feature.Feature>` to the layer.

    *o* is the feature to add. It may be specified directly as a Feature object or alternatively as a ``dict`` 
    or a ``list``.

    >>> from geoscript import geom
    >>> from geoscript import feature
    >>> l = Layer() 
    >>> l.count()
    0
    >>> f = feature.Feature({'geom': geom.Point(1,2)})
    >>> l.add(f)
    >>> l.count()
    1
    >>> l = Layer()
    >>> l.add({'geom': geom.Point(1,2)})
    >>> l.add([geom.Point(1,2)])
    >>> l.count()
    2
    """
    if isinstance(o, Layer):
      for f in o.features():
        self.add(f)
      return

    if isinstance(o, feature.Feature):
      f = o
      if not f.schema:
        f.schema = self.schema
    elif isinstance(o, (dict,list)):
      f = self.schema.feature(o)
      
    fc = FeatureCollections.newCollection() 
    fc.add(f._feature)
    self._source.addFeatures(fc)

  def reproject(self, prj, name=None, chunk=1000):
    """
    Reprojects a layer.

    *prj* is the destination :class:`Projection <geoscript.proj.Projection>` 

    *name* is the optional name as a ``str`` to assign to the resulting reprojected layer.

    This method returns a newly reprojected layer. The new layer is create within the containing workspace of the original layer.

    >>> from geoscript import geom
    >>> l = Layer()
    >>> l.proj = 'epsg:4326'
    >>> l.add([geom.Point(-111, 45.7)])
    >>> 
    >>> l2 = l.reproject('epsg:26912')
    >>> l2.proj.id
    'EPSG:26912'

    >>> [f.geom.round() for f in l2.features()]
    [POINT (500000 5060716)]
    """

    prj = proj.Projection(prj)
    name = name or Layer._newname()

    # reproject the schema
    rschema = self.schema.reproject(prj, name)

    # create the reprojected layer
    rlayer = self.workspace.create(schema=rschema)

    # create a query specifying that feautres should be reprojected
    q = DefaultQuery(self.name, Filter.PASS._filter)
    if self.proj:
      q.coordinateSystem = self.proj._crs
    q.coordinateSystemReproject = prj._crs 

    # loop through features and add to new reprojeced layer
    fit = self._source.getFeatures(q).features()
    try:
      while True:
        features = readFeatures(fit, self._source.getSchema(), chunk)
        if features.isEmpty(): 
          break
  
        rlayer._source.addFeatures(features)
    finally:
      fit.close()
    return rlayer

  def filter(self, fil, name=None):
    """
    Filters the layer.

    *fil* is the :class:`Filter <geoscript.filter.Filter>` to apply.

    *name* is the optional name to assign to the new filtered layer.

    This method returns a newly filtered layer. The new layer is create within the containing workspace of the original layer.

    >>> from geoscript.feature import Schema
    >>> l = Layer(schema=Schema('original', [('name', str)]))
    >>> l.add(['foo'])
    >>> l.add(['bar'])
    >>> l.add(['baz'])
    >>> 
    >>> l2 = l.filter("name = 'foo'", "filtered")
    >>> l2.count()
    1
    >>> l3 = l.filter("name LIKE 'b%'", "filtered2")
    >>> l3.count()
    2
    """

    f = Filter(fil)
    name = name or Layer._newname()
    fschema = feature.Schema(name, self.schema.fields)

    # create the filtered layer
    flayer = self.workspace.create(schema=fschema)

    q = DefaultQuery(self.name, f._filter)

    # loop through features and add to new filtered layer
    fit = self._source.getFeatures(q).features()
    try:
      while fit.hasNext():
        f = feature.Feature(schema=fschema, f=fit.next())
        flayer.add(f)
    finally:
      fit.close()

    return flayer

  def interpolate(self, att, classes=10, method='linear'):
    """
    Generates a set of interpolated values for an attribute of the layer.

    *att* specifies the attribute. *classes* specifies the number of values
    to generate.

    The *method* parameter specifies the interpolation method. By default
    a linear method is used. The values 'exp' (exponential) and 'log' 
    (logarithmic) methods are also supported.
   
    """
    min, max = self.minmax(att)
  
    delta = max-min
    if method == 'linear':
      fx = lambda x: delta * x
    elif method == 'exp':
      fx = lambda x: math.exp(x * math.log(1+delta)) - 1
    elif method == 'log':
      fx = lambda x: delta * math.log((x+1))/math.log(2)
    else:
      raise Exception('Interpolation method %s not supported' % method)
      
    fy = lambda x : min + fx(x)
    return map(fy, [x/float(classes) for x in range(0,classes+1)])

  def histogram(self, att, classes=10):
    """
    Generates the histogram of values for an attribute of the layer.

    *att* specifies the value to generate the histogram for and  *classes* 
    specifies the number of buckets to use. 

    This method returns a `list` of `tuple`, one tuple for each bucket. The
    first value of each tuple is another `tuple` representing the bucket 
    range, the second value is the number of values within that range.
    
    """
    low, high = self.minmax(att)

    rnge = high - low
    dx = rnge/float(classes)
    values = [0]*classes

    fil = Filter('%s BETWEEN %s AND %s' % (att, low, high))
    fit = self._source.getFeatures(fil._filter).features()
    try:
      while fit.hasNext():
        f = fit.next()
        val = f.getAttribute(att)
        #import pdb; pdb.set_trace()
        values[min(classes-1, int( (val-low)/float(rnge)*classes ))] += 1
    finally:
      fit.close()

    keys = [round(low + x * dx, 2) for x in range(0,classes+1)]
    return zip([(keys[i-1],keys[i]) for i in range(1,len(keys))], values)

  def minmax(self, att, low=None, high=None):
    """
    Calculates the minimum and maximum values for an attribute of the layer.

    *att* specifies the attribute. *low* and *high* are used to constrain
    the value space. 
    """
    # build a filter based on specified min/max values
    fil = ['%s >= %s' % (att, low)] if low != None else []
    fil += ['%s <= %s' % (att, high)] if high != None else []
    fil = ' AND '.join(fil)
     
    q = DefaultQuery(self.name)
    if len(fil) > 0:
      q.setFilter(Filter(fil)._filter)

    min, max = None, None
    fit = self._source.getFeatures(q).features()
    try:
      while fit.hasNext():
        f = fit.next() 
        val = f.getAttribute(att)
        min = val if min == None or val < min else min
        max = val if max == None or val > max else max
    finally:
      fit.close()

    return (min,max)

  def __eq__(self, other):
    return other and self.schema == other.schema

  def toGML(self,out=sys.stdout):
    try:
      from net.opengis.wfs import WfsFactory
      from org.geotools.wfs.v1_1 import WFS, WFSConfiguration
      from org.geotools.xml import Encoder
    except ImportError:
      raise Exception('toGML() not available, GML libraries not on classpath.') 

    features = self._source.features
    fc = WfsFactory.eINSTANCE.createFeatureCollectionType()
    fc.feature.add(features)

    e = Encoder(WFSConfiguration())        
    uri = self._source.name.namespaceURI
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
      features = self._source.features
      w = GeoJSONWriter() 
      w.write(features,out)

  @staticmethod
  def _newname():
    Layer._id  += 1
    return 'layer_%d' % Layer._id

core.registerTypeMapping(FeatureSource, Layer, lambda x: Layer(fs=x))
core.registerTypeUnmapping(Layer, FeatureSource, lambda x: x._source)
