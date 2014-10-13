# Licensed under a 3-clause BSD style license - see LICENSE.rst
from __future__ import (absolute_import, division, print_function,
                        unicode_literals)
import numpy as np
from astropy.table import Table, Column
from astropy.utils import lazyproperty
import astropy.units as u
from astropy.wcs.utils import pixel_to_skycoord
from .utils.prepare_data import _prepare_data


__all__ = ['SegmentProperties', 'segment_properties', 'properties_table',
           'relabel_sequential', 'relabel_segments', 'remove_segments',
           'remove_border_segments']

__doctest_requires__ = {('segment_properties', 'properties_table'): ['scipy'],
                        ('segment_properties', 'properties_table'):
                        ['skimage']}


class SegmentProperties(object):
    """
    Class to calculate photometry and morphological properties of source
    segments.
    """

    def __init__(self, data, segment_image, label, label_slice=None,
                 error=None, effective_gain=None, mask=None, background=None,
                 wcs=None):
        """
        Parameters
        ----------
        data : array_like or `~astropy.units.Quantity`
            The 2D array from which to calculate the source photometry
            and properties.

        segment_image : array_like (int)
            A 2D segmentation image, with the same shape as ``data``,
            where sources are marked by different positive integer
            values.  A value of zero is reserved for the background.

        label : int
            The label number of a source segment in ``segment_image``
            for which to calculate properties.

        label_slice : 2-tuple of slice objects, optional
            A ``(y_slice, x_slice)`` tuple of slice objects defining the
            minimal box enclosing the source segment.  If `None` (the
            default), then ``label_slice`` will be calculated.

        error : array_like or `~astropy.units.Quantity`, optional
            The pixel-wise Gaussian 1-sigma errors of the input
            ``data``.  If ``effective_gain`` is input, then ``error``
            should include all sources of "background" error but
            *exclude* the Poisson error of the sources.  If
            ``effective_gain`` is `None`, then the ``error_image`` is
            assumed to include *all* sources of error, including the
            Poisson error of the sources.  ``error`` must have the same
            shape as ``data``.  See the Notes section below for details
            on the error propagation.

        effective_gain : float, array-like, or `~astropy.units.Quantity`, optional
            Ratio of counts (e.g., electrons or photons) to the units of
            ``data`` used to calculate the Poisson error of the sources.
            If ``effective_gain`` is `None`, then the ``error`` is
            assumed to include *all* sources of error.  See the Notes
            section below for details on the error propagation.

        mask : array_like (bool), optional
            A boolean mask, with the same shape as ``data``, where a
            `True` value indicates the corresponding element of ``data``
            is masked.  Masked data are excluded/ignored from all
            calculations.

        background : float, array_like, or `~astropy.units.Quantity`, optional
            The background level of the input ``data``.  ``background``
            may either be a scalar value or a 2D image with the same
            shape as the input ``data``.  If the input ``data`` has been
            background-subtracted, then set ``background`` to `None`
            (the default) or ``0.``.

        wcs : `~astropy.wcs.WCS`
            The WCS transformation to use.  If `None`, then
            ``icrs_centroid``, ``ra_centroid``, and ``dec_centroid``
            will be `None`.

        Notes
        -----
        If ``effective_gain`` is input, then ``error`` should include
        all sources of "background" error but *exclude* the Poisson
        error of the sources.  The total error image,
        :math:`\sigma_{\mathrm{tot}}` is then:

        .. math:: \\sigma_{\\mathrm{tot}} = \\sqrt{\\sigma_{\\mathrm{b}}^2 +
                      \\frac{I}{g}}

        where :math:`\sigma_b`, :math:`I`, and :math:`g` are the
        background ``error`` image, ``data`` image, and
        ``effective_gain``, respectively.

        If ``effective_gain`` is `None`, then ``error`` is assumed to
        include *all* sources of error, including the Poisson error of
        the sources, i.e. :math:`\sigma_{\mathrm{tot}} =
        \mathrm{error}`.

        For example, if your input ``data`` are in units of ADU, then
        ``effective_gain`` should represent electrons/ADU.  If your
        input ``data`` are in units of electrons/s then
        ``effective_gain`` should be the exposure time.

        `~photutils.SegmentProperties.segment_sum_err` is simply the
        quadrature sum of the pixel-wise total errors over the
        non-masked pixels within the source segment:

        .. math:: \\Delta F = \\sqrt{\\sum_{i \\in S}
                  \\sigma_{\\mathrm{tot}, i}^2}

        where :math:`\Delta F` is
        `~photutils.SegmentProperties.segment_sum_err` and :math:`S` are
        the non-masked pixels in the source segment.

        Custom errors for source segments can be calculated using the
        `~photutils.SegmentProperties.error_cutout_ma` and
        `~photutils.SegmentProperties.background_cutout_ma` properties,
        which are 2D `~numpy.ma.MaskedArray` cutout versions of the
        input ``error`` and ``background``.  The mask is `True` for both
        pixels outside of the source segment and masked pixels.
        """

        from scipy import ndimage

        if segment_image.shape != data.shape:
            raise ValueError('segment_image and data must have the same '
                             'shape')
        if mask is not None:
            if mask.shape != data.shape:
                raise ValueError('mask and data must have the same shape')

        if label == 0:
            raise ValueError('label "0" is reserved for the background')
        elif label < 0:
            raise ValueError('label must be a positive integer')

        self._inputimage = data
        self._segment_image = segment_image
        image, error, background = _prepare_data(
            data, error=error, effective_gain=effective_gain,
            background=background)
        self._image = image    # background subtracted
        self._error = error    # total error
        self._background = background    # 2D error array
        self._mask = mask
        self._wcs = wcs

        self.label = label
        if label_slice is not None:
            self._slice = label_slice
        else:
            label_slices = ndimage.find_objects(segment_image)
            self._slice = label_slices[label - 1]
            if self._slice is None:
                raise ValueError('label "{0}" is not in the input '
                                 'segment_image'.format(label))

    def __getitem__(self, key):
        return getattr(self, key, None)

    def to_table(self, columns=None, exclude_columns=None):
        """
        Return a `~astropy.table.Table` of properties.

        If ``columns`` or ``exclude_columns`` are not input, then the
        `~astropy.table.Table` will include all scalar-valued
        properties.  Multi-dimensional properties, e.g.
        `~photutils.SegmentProperties.data_cutout`, can be included in
        the ``columns`` input.

        Parameters
        ----------
        columns : str or list of str, optional
            Names of columns, in order, to include in the output
            `~astropy.table.Table`.  The allowed column names are any of
            the attributes of `SegmentProperties`.

        exclude_columns : str or list of str, optional
            Names of columns to exclude from the default properties list
            in the output `~astropy.table.Table`.  The default
            properties are those with scalar values.

        Returns
        -------
        table : `~astropy.table.Table`
            A single-row table of properties of the segmented source.
        """
        return properties_table(self, columns=columns,
                                exclude_columns=exclude_columns)

    @lazyproperty
    def _in_segment(self):
        """
        _in_segment is `True` for pixels in the labeled source segment.
        """
        return self._segment_image[self._slice] == self.label

    @lazyproperty
    def _local_mask(self):
        """
        _local_mask is `True` for regions outside of the labeled source
        segment or where the input mask is `True`.
        """
        if self._mask is None:
            return ~self._in_segment
        else:
            return np.logical_or(~self._in_segment, self._mask[self._slice])

    @lazyproperty
    def data_cutout(self):
        """
        A 2D cutout from the data of the source segment.
        """
        return self._inputimage[self._slice]

    @lazyproperty
    def data_cutout_ma(self):
        """
        A 2D `~numpy.ma.MaskedArray` cutout from the data, where the
        mask is `True` for both pixels outside of the source segment and
        masked pixels.
        """
        return np.ma.masked_array(self.data_cutout, mask=self._local_mask)

    @lazyproperty
    def _data_cutout_maskzeroed_double(self):
        """
        A 2D cutout from the data, where pixels outside of the source
        segment and masked pixels are set to zero.  The cutout image is
        double precision, which is required for scikit-image's Cython
        moment functions.
        """
        return (self.data_cutout * ~self._local_mask).astype(np.float64)

    @lazyproperty
    def error_cutout_ma(self):
        """
        A 2D `~numpy.ma.MaskedArray` cutout from the input ``error``
        image, where the mask is `True` for both pixels outside of the
        source segment and masked pixels.  If ``error`` is `None`, then
        ``error_cutout_ma`` is also `None`.
        """
        if self._error is not None:
            return np.ma.masked_array(self._error[self._slice],
                                      mask=self._local_mask)
        else:
            return None

    @lazyproperty
    def background_cutout_ma(self):
        """
        A 2D `~numpy.ma.MaskedArray` cutout from the input
        ``background``, where the mask is `True` for both pixels outside
        of the source segment and masked pixels.  If ``background`` is
        `None`, then ``background_cutout_ma`` is also `None`.
        """
        if self._background is not None:
            return np.ma.masked_array(self._background[self._slice],
                                      mask=self._local_mask)
        else:
            return None

    @lazyproperty
    def coords(self):
        """
        A list of the ``(y, x)`` pixel coordinates of the source
        segment.  Masked pixels are not included.
        """
        yy, xx = np.nonzero(self.data_cutout_ma)
        coords = (yy + self._slice[0].start, xx + self._slice[1].start)
        return coords

    @lazyproperty
    def values(self):
        """
        A list of the pixel values within the source segment.  Masked
        pixels are not included.
        """
        return self.data_cutout[~self._local_mask]

    @lazyproperty
    def moments(self):
        """Spatial moments up to 3rd order of the source segment."""
        from skimage.measure import moments
        return moments(self._data_cutout_maskzeroed_double, 3)

    @lazyproperty
    def moments_central(self):
        """
        Central moments (translation invariant) of the source segment up
        to 3rd order.
        """
        from skimage.measure import moments_central
        ycentroid, xcentroid = self.local_centroid.value
        return moments_central(self._data_cutout_maskzeroed_double,
                               ycentroid, xcentroid, 3)

    @lazyproperty
    def id(self):
        """
        The source identification number corresponding to the object
        label in the ``segment_image``.
        """
        return self.label

    @lazyproperty
    def local_centroid(self):
        """
        The ``(y, x)`` coordinate, relative to the `data_cutout`, of
        the centroid within the source segment.
        """
        # TODO: allow alternative centroid methods?
        m = self.moments
        ycentroid = m[0, 1] / m[0, 0]
        xcentroid = m[1, 0] / m[0, 0]
        return (ycentroid, xcentroid) * u.pix

    @lazyproperty
    def centroid(self):
        """
        The ``(y, x)`` coordinate of the centroid within the source
        segment.
        """
        ycen, xcen = self.local_centroid.value
        return (ycen + self._slice[0].start,
                xcen + self._slice[1].start) * u.pix

    @lazyproperty
    def xcentroid(self):
        """
        The ``x`` coordinate of the centroid within the source segment.
        """
        return self.centroid[1]

    @lazyproperty
    def ycentroid(self):
        """
        The ``y`` coordinate of the centroid within the source segment.
        """
        return self.centroid[0]

    @lazyproperty
    def icrs_centroid(self):
        """
        The ICRS coordinates of the centroid within the source segment,
        returned as a `~astropy.coordinated.SkyCoord` object.
        """
        if self._wcs is not None:
            return pixel_to_skycoord(self.xcentroid.value,
                                     self.ycentroid.value,
                                     self._wcs, origin=1).icrs
        else:
            return None

    @lazyproperty
    def ra_centroid(self):
        """
        The ICRS Right Ascension coordinate (in degrees) of the centroid
        within the source segment.
        """
        if self._wcs is not None:
            return self.icrs_centroid.ra.degree * u.deg
        else:
            return None

    @lazyproperty
    def dec_centroid(self):
        """
        The ICRS Declination coordinate (in degrees) of the centroid
        within the source segment.
        """
        if self._wcs is not None:
            return self.icrs_centroid.dec.degree * u.deg
        else:
            return None

    @lazyproperty
    def bbox(self):
        """
        The bounding box ``(ymin, xmin, ymax, xmax)`` of the minimal
        rectangular region containing the source segment.
        """
        # (stop - 1) to return the max pixel location, not the slice index
        return (self._slice[0].start, self._slice[1].start,
                self._slice[0].stop - 1, self._slice[1].stop - 1) * u.pix

    @lazyproperty
    def xmin(self):
        """
        The left ``x`` pixel location of the minimal bounding box
        (`~photutils.SegmentProperties.bbox`) of the source segment.
        """
        return self.bbox[1]

    @lazyproperty
    def xmax(self):
        """
        The right ``x`` pixel location of the minimal bounding box
        (`~photutils.SegmentProperties.bbox`) of the source segment.
        """
        return self.bbox[3]

    @lazyproperty
    def ymin(self):
        """
        The bottom ``y`` pixel location of the minimal bounding box
        (`~photutils.SegmentProperties.bbox`) of the source segment.
        """
        return self.bbox[0]

    @lazyproperty
    def ymax(self):
        """
        The top ``y`` pixel location of the minimal bounding box
        (`~photutils.SegmentProperties.bbox`) of the source segment.
        """
        return self.bbox[2]

    @lazyproperty
    def min_value(self):
        """The minimum pixel value within the source segment."""
        return np.min(self.values)

    @lazyproperty
    def max_value(self):
        """The maximum pixel value within the source segment."""
        return np.max(self.values)

    @lazyproperty
    def minval_local_pos(self):
        """
        The ``(y, x)`` coordinate, relative to the `data_cutout`, of
        the minimum pixel value.
        """
        return np.argwhere(self.data_cutout_ma == self.min_value)[0] * u.pix

    @lazyproperty
    def maxval_local_pos(self):
        """
        The ``(y, x)`` coordinate, relative to the `data_cutout`, of
        the maximum pixel value.
        """
        return np.argwhere(self.data_cutout_ma == self.max_value)[0] * u.pix

    @lazyproperty
    def minval_pos(self):
        """The ``(y, x)`` coordinate of the minimum pixel value."""
        yp, xp = np.array(self.minval_local_pos)
        return (yp + self._slice[0].start, xp + self._slice[1].start) * u.pix

    @lazyproperty
    def maxval_pos(self):
        """The ``(y, x)`` coordinate of the maximum pixel value."""
        yp, xp = np.array(self.maxval_local_pos)
        return (yp + self._slice[0].start, xp + self._slice[1].start) * u.pix

    @lazyproperty
    def minval_xpos(self):
        """The ``x`` coordinate of the minimum pixel value."""
        return self.minval_pos[1]

    @lazyproperty
    def minval_ypos(self):
        """The ``y`` coordinate of the minimum pixel value."""
        return self.minval_pos[0]

    @lazyproperty
    def maxval_xpos(self):
        """The ``x`` coordinate of the maximum pixel value."""
        return self.maxval_pos[1]

    @lazyproperty
    def maxval_ypos(self):
        """The ``y`` coordinate of the maximum pixel value."""
        return self.maxval_pos[0]

    @lazyproperty
    def area(self):
        """The area of the source segment in units of pixels**2."""
        return len(self.values) * u.pix**2

    @lazyproperty
    def equivalent_radius(self):
        """
        The radius of a circle with the same `area` as the source
        segment.
        """
        return np.sqrt(self.area / np.pi)

    @lazyproperty
    def perimeter(self):
        """
        The perimeter of the source segment, approximated using a line
        through the centers of the border pixels using a 4-connectivity.
        """
        from skimage.measure import perimeter
        return perimeter(self._in_segment, 4) * u.pix

    @lazyproperty
    def inertia_tensor(self):
        """
        Inertia tensor of the source segment for the rotation around its
        center of mass.
        """
        mu = self.moments_central
        a = mu[2, 0]
        b = -mu[1, 1]
        c = mu[0, 2]
        return np.array([[a, b], [b, c]]) * u.pix**2

    @lazyproperty
    def covariance(self):
        """
        The covariance matrix of the 2D Gaussian function that has the
        same second-order moments as the source segment.
        """
        mu = self.moments_central
        m = mu / mu[0, 0]
        return np.array([[m[2, 0], m[1, 1]], [m[1, 1], m[0, 2]]]) * u.pix**2

    @lazyproperty
    def covariance_eigvals(self):
        """
        The two eigenvalues of the `covariance` matrix in decreasing
        order.
        """
        eigvals = np.linalg.eigvals(self.covariance)
        return (np.max(eigvals), np.min(eigvals)) * u.pix**2

    @lazyproperty
    def semimajor_axis_sigma(self):
        """
        The 1-sigma standard deviation along the semimajor axis of the
        2D Gaussian function that has the same second-order central
        moments as the source segment.
        """
        # this matches SExtractor's A parameter
        return np.sqrt(self.covariance_eigvals[0])

    @lazyproperty
    def semiminor_axis_sigma(self):
        """
        The 1-sigma standard deviation along the semiminor axis of the
        2D Gaussian function that has the same second-order central
        moments as the source segment.
        """
        # this matches SExtractor's B parameter
        return np.sqrt(self.covariance_eigvals[1])

    @lazyproperty
    def eccentricity(self):
        """
        The eccentricity of the 2D Gaussian function that has the same
        second-order moments as the source segment.

        The eccentricity is the fraction of the distance along the
        semimajor axis at which the focus lies.

        .. math:: e = \\sqrt{1 - \\frac{b^2}{a^2}}

        where :math:`a` and :math:`b` are the lengths of the semimajor
        and semiminor axes, respectively.
        """
        l1, l2 = self.covariance_eigvals
        if l1 == 0:
            return 0.
        return np.sqrt(1. - (l2 / l1))

    @lazyproperty
    def orientation(self):
        """
        The angle in radians between the ``x`` axis and the major axis
        of the 2D Gaussian function that has the same second-order
        moments as the source segment.  The angle increases in the
        counter-clockwise direction.
        """
        a, b, b, c = self.covariance.flat
        return 0.5 * np.arctan2(2. * b, (a - c))

    @lazyproperty
    def elongation(self):
        """
        SExtractor's elongation parameter.

        .. math:: \mathrm{elongation} = \\frac{a}{b}

        where :math:`a` and :math:`b` are the lengths of the semimajor
        and semiminor axes, respectively.
        """
        return self.semimajor_axis_sigma / self.semiminor_axis_sigma

    @lazyproperty
    def ellipticity(self):
        """
        SExtractor's ellipticity parameter.

        .. math:: \mathrm{ellipticity} = 1 - \\frac{b}{a}

        where :math:`a` and :math:`b` are the lengths of the semimajor
        and semiminor axes, respectively.
        """
        return 1.0 - (self.semiminor_axis_sigma / self.semimajor_axis_sigma)

    @lazyproperty
    def se_x2(self):
        """
        SExtractor's X2 parameter, in units of pixel**2, which
        corresponds to the ``(0, 0)`` element of the `covariance`
        matrix.
        """
        return self.covariance[0, 0]

    @lazyproperty
    def se_y2(self):
        """
        SExtractor's Y2 parameter, in units of pixel**2, which
        corresponds to the ``(1, 1)`` element of the `covariance`
        matrix.
        """
        return self.covariance[1, 1]

    @lazyproperty
    def se_xy(self):
        """
        SExtractor's XY parameter, in units of pixel**2, which
        corresponds to the ``(0, 1)`` element of the `covariance`
        matrix.
        """
        return self.covariance[0, 1]

    @lazyproperty
    def se_cxx(self):
        """
        SExtractor's CXX ellipse parameter in units of pixel**(-2).
        """
        return ((np.cos(self.orientation) / self.semimajor_axis_sigma)**2 +
                (np.sin(self.orientation) / self.semiminor_axis_sigma)**2)

    @lazyproperty
    def se_cyy(self):
        """
        SExtractor's CYY ellipse parameter in units of pixel**(-2).
        """
        return ((np.sin(self.orientation) / self.semimajor_axis_sigma)**2 +
                (np.cos(self.orientation) / self.semiminor_axis_sigma)**2)

    @lazyproperty
    def se_cxy(self):
        """
        SExtractor's CXY ellipse parameter in units of pixel**(-2).
        """
        return (2. * np.cos(self.orientation) * np.sin(self.orientation) *
                ((1. / self.semimajor_axis_sigma**2) -
                 (1. / self.semiminor_axis_sigma**2)))

    @lazyproperty
    def segment_sum(self):
        """
        The sum of the non-masked background-subtracted data values
        within the source segment.

        .. math:: F = \\sum_{i \\in S} (I_i - B_i)

        where :math:`F` is ``segment_sum``, :math:`I_i` is the ``data``,
        :math:`B_i` is the ``background``, and :math:`S` are the
        non-masked pixels in the source segment.
        """
        return np.sum(np.ma.masked_array(self._image[self._slice],
                                         mask=self._local_mask))

    @lazyproperty
    def segment_sum_err(self):
        """
        The uncertainty of `~photutils.SegmentProperties.segment_sum`,
        propagated from the input ``error`` array.

        ``segment_sum_err`` is the quadrature sum of the total errors
        over the non-masked pixels within the source segment:

        .. math:: \\Delta F = \\sqrt{\\sum_{i \\in S}
                  \\sigma_{\\mathrm{tot}, i}^2}

        where :math:`\Delta F` is ``segment_sum_err``,
        :math:`\sigma_{\mathrm{tot, i}}` are the pixel-wise total
        errors, and :math:`S` are the non-masked pixels in the source
        segment.
        """
        if self._error is not None:
            # power doesn't work here, see astropy #2968
            # return np.sqrt(np.sum(self.error_cutout_ma**2))
            return np.sqrt(np.sum(
                np.ma.masked_array(self.error_cutout_ma.data**2,
                                   mask=self.error_cutout_ma.mask)))
        else:
            return None

    @lazyproperty
    def background_sum(self):
        """The sum of ``background`` values within the source segment."""
        if self._background is not None:
            return np.sum(self.background_cutout_ma)
        else:
            return None

    @lazyproperty
    def background_mean(self):
        """The mean of ``background`` values within the source segment."""
        if self._background is not None:
            return np.mean(self.background_cutout_ma)
        else:
            return None

    @lazyproperty
    def background_atcentroid(self):
        """
        The value of the ``background`` at the position of the source
        centroid.
        """
        if self._background is None:
            return None
        else:
            return self._background[self.ycentroid.value,
                                    self.xcentroid.value]


def segment_properties(data, segment_image, error=None, effective_gain=None,
                       mask=None, background=None, wcs=None, labels=None):
    """
    Calculate photometry and morphological properties of sources defined
    by a labeled segmentation image.

    Parameters
    ----------
    data : array_like or `~astropy.units.Quantity`
        The 2D array from which to calculate the source photometry and
        properties.

    segment_image : array_like (int)
        A 2D segmentation image, with the same shape as ``data``, where
        sources are marked by different positive integer values.  A
        value of zero is reserved for the background.

    error : array_like or `~astropy.units.Quantity`, optional
        The pixel-wise Gaussian 1-sigma errors of the input ``data``.
        If ``effective_gain`` is input, then ``error`` should include
        all sources of "background" error but *exclude* the Poisson
        error of the sources.  If ``effective_gain`` is `None`, then the
        ``error_image`` is assumed to include *all* sources of error,
        including the Poisson error of the sources.  ``error`` must have
        the same shape as ``data``.  See the Notes section below for
        details on the error propagation.

    effective_gain : float, array-like, or `~astropy.units.Quantity`, optional
        Ratio of counts (e.g., electrons or photons) to the units of
        ``data`` used to calculate the Poisson error of the sources.  If
        ``effective_gain`` is `None`, then the ``error`` is assumed to
        include *all* sources of error.  See the Notes section below for
        details on the error propagation.

    mask : array_like (bool), optional
        A boolean mask, with the same shape as ``data``, where a `True`
        value indicates the corresponding element of ``data`` is masked.
        Masked data are excluded/ignored from all calculations.

    background : float, array-like, or `~astropy.units.Quantity`, optional
        The background level of the input ``data``.  ``background`` may
        either be a scalar value or a 2D image with the same shape as
        the input ``data``.  If the input ``data`` has been
        background-subtracted, then set ``background`` to `None` (the
        default).

    wcs : `~astropy.wcs.WCS`
        The WCS transformation to use.  If `None`, then the
        ``ra_centroid`` and ``dec_centroid`` columns will contain
        `None`s.

    labels : int or list of ints
        Subset of ``segment_image`` labels for which to calculate the
        properties.  If `None`, then the properties will be calculated
        for all source segments (the default).

    Returns
    -------
    output : list of `SegmentProperties` objects
        A list of `SegmentProperties` objects, one for each source
        segment.  The properties can be accessed as attributes or keys.

    Notes
    -----
    If ``effective_gain`` is input, then ``error`` should include all
    sources of "background" error but *exclude* the Poisson error of the
    sources.  The total error image, :math:`\sigma_{\mathrm{tot}}` is
    then:

    .. math:: \\sigma_{\\mathrm{tot}} = \\sqrt{\\sigma_{\\mathrm{b}}^2 +
                  \\frac{I}{g}}

    where :math:`\sigma_b`, :math:`I`, and :math:`g` are the background
    ``error`` image, ``data`` image, and ``effective_gain``,
    respectively.

    If ``effective_gain`` is `None`, then ``error`` is assumed to
    include *all* sources of error, including the Poisson error of the
    sources, i.e. :math:`\sigma_{\mathrm{tot}} = \mathrm{error}`.

    For example, if your input ``data`` are in units of ADU, then
    ``effective_gain`` should represent electrons/ADU.  If your input
    ``data`` are in units of electrons/s then ``effective_gain`` should
    be the exposure time.

    See Also
    --------
    detect_sources, properties_table

    Examples
    --------
    >>> import numpy as np
    >>> from photutils import segment_properties
    >>> image = np.arange(16.).reshape(4, 4)
    >>> segm_image = np.array([[1, 1, 0, 0],
    ...                        [1, 0, 0, 2],
    ...                        [0, 0, 2, 2],
    ...                        [0, 2, 2, 0]])
    >>> props = segment_properties(image, segm_image)

    Print some properties of the first object (labeled with ``1`` in the
    segmentation image):

    >>> print(props[0].id)    # id corresponds to segment label number
    1
    >>> print(props[0].centroid)    # doctest: +FLOAT_CMP
    [ 0.8  0.2] pix
    >>> print(props[0].segment_sum)    # doctest: +FLOAT_CMP
    5.0
    >>> print(props[0].area)    # doctest: +FLOAT_CMP
    3.0 pix2
    >>> print(props[0].max_value)    # doctest: +FLOAT_CMP
    4.0

    Print some properties of the second object (labeled with ``2`` in
    the segmentation image):

    >>> print(props[1].id)    # id corresponds to segment label number
    2
    >>> print(props[1].centroid)    # doctest: +FLOAT_CMP
    [ 2.36363636  2.09090909] pix
    >>> print(props[1].perimeter)    # doctest: +FLOAT_CMP
    5.41421356237 pix
    >>> print(props[1].orientation)    # doctest: +FLOAT_CMP
    -0.741759306923 rad
    """

    from scipy import ndimage

    if segment_image.shape != data.shape:
        raise ValueError('segment_image and data must have the same shape')

    if labels is None:
        label_ids = np.unique(segment_image[segment_image > 0])
    else:
        label_ids = np.atleast_1d(labels)

    label_slices = ndimage.find_objects(segment_image)
    segm_propslist = []
    for i, label_slice in enumerate(label_slices):
        label = i + 1    # consecutive even if some label numbers are missing
        # label_slice is None for missing label numbers
        if label_slice is None or label not in label_ids:
            continue
        segm_props = SegmentProperties(
            data, segment_image, label, label_slice=label_slice, error=error,
            effective_gain=effective_gain, mask=mask, background=background,
            wcs=wcs)
        segm_propslist.append(segm_props)
    return segm_propslist


def properties_table(segment_props, columns=None, exclude_columns=None):
    """
    Construct an `~astropy.table.Table` of properties from a list of
    `SegmentProperties` objects.

    If ``columns`` or ``exclude_columns`` are not input, then the
    `~astropy.table.Table` will include all scalar-valued properties.
    Multi-dimensional properties, e.g.
    `~photutils.SegmentProperties.data_cutout`, can be included in the
    ``columns`` input.

    Parameters
    ----------
    segment_props : `SegmentProperties` or list of `SegmentProperties`
        A `SegmentProperties` object or list of `SegmentProperties`
        objects, one for each source segment.

    columns : str or list of str, optional
        Names of columns, in order, to include in the output
        `~astropy.table.Table`.  The allowed column names are any of the
        attributes of `SegmentProperties`.

    exclude_columns : str or list of str, optional
        Names of columns to exclude from the default properties list in
        the output `~astropy.table.Table`.  The default properties are
        those with scalar values.

    Returns
    -------
    table : `~astropy.table.Table`
        A table of properties of the segmented sources, one row per
        source segment.

    See Also
    --------
    detect_sources, segment_properties

    Examples
    --------
    >>> import numpy as np
    >>> from photutils import segment_properties, properties_table
    >>> image = np.arange(16.).reshape(4, 4)
    >>> segm_image = np.array([[1, 1, 0, 0],
    ...                        [1, 0, 0, 2],
    ...                        [0, 0, 2, 2],
    ...                        [0, 2, 2, 0]])
    >>> segm_props = segment_properties(image, segm_image)
    >>> columns = ['id', 'xcentroid', 'ycentroid', 'segment_sum']
    >>> t = properties_table(segm_props, columns=columns)
    >>> print(t)
     id   xcentroid     ycentroid   segment_sum
             pix           pix
    --- ------------- ------------- -----------
      1           0.2           0.8         5.0
      2 2.09090909091 2.36363636364        55.0
    """

    if isinstance(segment_props, list) and len(segment_props) == 0:
        raise ValueError('segment_props is an empty list')
    segment_props = np.atleast_1d(segment_props)

    props_table = Table()
    # all scalar-valued properties
    columns_all = ['id', 'xcentroid', 'ycentroid', 'ra_centroid',
                   'dec_centroid', 'segment_sum',
                   'segment_sum_err', 'background_sum', 'background_mean',
                   'background_atcentroid', 'xmin', 'xmax', 'ymin', 'ymax',
                   'min_value', 'max_value', 'minval_xpos', 'minval_ypos',
                   'maxval_xpos', 'maxval_ypos', 'area', 'equivalent_radius',
                   'perimeter', 'semimajor_axis_sigma',
                   'semiminor_axis_sigma', 'eccentricity', 'orientation',
                   'ellipticity', 'elongation', 'se_x2', 'se_xy',
                   'se_y2', 'se_cxx', 'se_cxy', 'se_cyy']

    table_columns = None
    if exclude_columns is not None:
        table_columns = [s for s in columns_all if s not in exclude_columns]

    if columns is not None:
        table_columns = np.atleast_1d(columns)

    if table_columns is None:
        table_columns = columns_all

    # it's *much* faster to calculate world coordinates using the
    # complete list of (x, y) instead of from the individual (x, y).
    # The assumption here is that the wcs is the same for each
    # element of segment_props.
    if 'ra_centroid' in table_columns or 'dec_centroid' in table_columns:
        xcentroid = [props.xcentroid.value for props in segment_props]
        ycentroid = [props.ycentroid.value for props in segment_props]
        if segment_props[0]._wcs is not None:
            skycoord = pixel_to_skycoord(xcentroid, ycentroid,
                segment_props[0]._wcs,
                origin=1).icrs
            ra = skycoord.ra.degree * u.deg
            dec = skycoord.dec.degree * u.deg
        else:
            nprops = len(segment_props)
            ra, dec = [None] * nprops, [None] * nprops

    for column in table_columns:
        if column == 'ra_centroid':
            props_table[column] = ra
        elif column == 'dec_centroid':
            props_table[column] = dec
        else:
            values = [getattr(props, column) for props in segment_props]
            if isinstance(values[0], u.Quantity):
                # turn list of Quantities into a Quantity array
                values = u.Quantity(values)
            props_table[column] = values

    return props_table


def relabel_sequential(segment_image, start_label=1):
    """
    Relabel the labels in a segmentation image sequentially, such that
    there are no missing label numbers.

    Parameters
    ----------
    segment_image : array_like (int)
        A 2D segmentation image where sources are marked by different
        positive integer values.  A value of zero is reserved for the
        background.

    start_label : int
        The starting label number, which should be strictly positive.

    Returns
    -------
    result : `~numpy.ndarray` (int)
        The relabeled segmentation image.

    Examples
    --------
    >>> from photutils.segmentation import relabel_sequential
    >>> segment_image = [[1, 1, 0],
    ...                  [1, 0, 3],
    ...                  [0, 3, 3]]
    >>> relabel_sequential(segment_image)
    array([[1, 1, 0],
           [1, 0, 2],
           [0, 2, 2]])
    """

    if start_label <= 0:
        raise ValueError('start_label must be >= 0.')
    segment_image = np.array(segment_image).astype(np.int)
    label_max = int(np.max(segment_image))
    labels = np.unique(segment_image[segment_image.nonzero()])
    if (label_max == len(labels)) and (labels[0] == start_label):
        return segment_image
    forward_map = np.zeros(label_max + 1, dtype=np.int)
    forward_map[labels] = np.arange(len(labels)) + start_label
    return forward_map[segment_image]


def relabel_segments(segment_image, labels, new_label):
    """
    Relabel labels in a segmentation image. ``labels`` will be relabeled
    to ``new_label``.

    Parameters
    ----------
    segment_image : array_like (int)
        A 2D segmentation image where sources are marked by different
        positive integer values.  A value of zero is reserved for the
        background.

    labels : int, array-like (1D, int)
        The label numbers(s) to relabel.

    new_label : int
        The relabeled label number.

    Returns
    -------
    result : `~numpy.ndarray` (int)
        The relabeled segmentation image.

    Examples
    --------
    >>> from photutils.segmentation import relabel_segments
    >>> segment_image = [[1, 1, 0],
    ...                  [0, 0, 3],
    ...                  [2, 0, 3]]
    >>> relabel_segments(segment_image, labels=[1, 3], new_label=5)
    array([[5, 5, 0],
           [0, 0, 5],
           [2, 0, 5]])
    """

    labels = np.atleast_1d(labels)
    segment_image = np.array(segment_image, copy=True).astype(np.int)
    for label in labels:
        segment_image[np.where(segment_image == label)] = new_label
    return segment_image


def remove_segments(segment_image, labels, relabel=False):
    """
    Remove labeled segments from a segmentation image.

    Parameters
    ----------
    segment_image : array_like (int)
        A 2D segmentation image where sources are marked by different
        positive integer values.  A value of zero is reserved for the
        background.

    labels : int, array-like (1D, int)
        The label numbers(s) of the segments to remove.  Labels of zero
        and those not in ``segment_image`` will be ignored.

    relabel : bool
        If `True`, the the segmentation image will be relabeled such
        that the labels are in sequential order.

    Returns
    -------
    result : `~numpy.ndarray` (int)
        The relabeled segmentation image.

    Examples
    --------
    >>> from photutils.segmentation import remove_segments
    >>> segment_image = [[1, 1, 0],
    ...                  [0, 0, 3],
    ...                  [2, 0, 3]]
    >>> remove_segments(segment_image, labels=2)
    array([[1, 1, 0],
           [0, 0, 3],
           [0, 0, 3]])
    """

    segment_out = relabel_segments(segment_image, labels, new_label=0)
    if relabel:
        segment_out = relabel_sequential(segment_out)
    return segment_out


def remove_border_segments(segment_image, border_width, partial_overlap=True,
                           relabel=False):
    """
    Remove labeled segments around the border of a segmentation image.

    Parameters
    ----------
    segment_image : array_like (int)
        A 2D segmentation image where sources are marked by different
        positive integer values.  A value of zero is reserved for the
        background.

    border_width : int
        The width of the border region in pixels.

    partial_overlap : bool, optional
        If this is set to `True` (the default), a segment that partially
        extends into the border region will also be removed.  Segments
        that are completely within the border region are always removed.

    relabel : bool
        If `True`, the the segmentation image will be relabeled such
        that the labels are in sequential order.

    Returns
    -------
    result : `~numpy.ndarray` (int)
        The relabeled segmentation image.

    Examples
    --------
    >>> from photutils.segmentation import remove_border_segments
    >>> segment_image = [[1, 1, 0, 4, 4],
    ...                  [0, 0, 0, 0, 0],
    ...                  [0, 0, 3, 0, 5],
    ...                  [2, 2, 0, 0, 5],
    ...                  [2, 2, 0, 5, 5]]
    >>> remove_border_segments(segment_image, border_width=1)
    array([[0, 0, 0, 0, 0],
           [0, 0, 0, 0, 0],
           [0, 0, 3, 0, 0],
           [0, 0, 0, 0, 0],
           [0, 0, 0, 0, 0]])
    >>> remove_border_segments(segment_image, border_width=1,
    ...                        partial_overlap=False)
    array([[0, 0, 0, 0, 0],
           [0, 0, 0, 0, 0],
           [0, 0, 3, 0, 0],
           [2, 2, 0, 0, 0],
           [2, 2, 0, 0, 0]])
    """

    segment_image = np.array(segment_image).astype(np.int)
    if border_width >= min(segment_image.shape) / 2:
        raise ValueError('border_width must be smaller than half the '
                         'image size in either dimension')
    border = np.zeros_like(segment_image, dtype=np.bool)
    border[:border_width, :] = True
    border[-border_width:, :] = True
    border[:, :border_width] = True
    border[:, -border_width:] = True
    labels = np.unique(segment_image[border])
    if not partial_overlap:
        inside_labels = np.unique(segment_image[~border])
        labels = [i for i in labels if i not in inside_labels]
    return remove_segments(segment_image, labels, relabel=relabel)