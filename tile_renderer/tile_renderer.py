#!/usr/bin/env python

from tile_data import TileData
import opencensus_json

class TileRenderer(object):
    def __init__(self, tile, db_cursor, projection, include_statistics=True):
        self.tile = tile
        self.db_cursor = db_cursor
        self.projection = projection
        self.include_statistics = include_statistics

    def _getBoundingBox(self, padding = 0.0):
        topLeftCoord = self.tile.getTopLeftCoord()
        bottomRightCoord = self.tile.getBottomRightCoord()

        if padding > 0.0:
            topLeftCoord = topLeftCoord.left(padding).up(padding)
            bottomRightCoord = bottomRightCoord.right(padding).down(padding)

        nw = self.projection.coordinateLocation(topLeftCoord)
        se = self.projection.coordinateLocation(bottomRightCoord)

        return ( nw, se )

    def _getBoundingBoxSQL(self, padding = 0.0):
        nw, se = self._getBoundingBox(padding)

        return 'ST_MakeBox2D(ST_Point(%.12f, %.12f), ST_Point(%.12f, %.12f))' % (nw.lon, nw.lat, se.lon, se.lat)

    def _getPolygonsSQL(self):
        nw, se = self._getBoundingBox()

        # We pad our response by 1% per side. That's about 2px per side. If we didn't, the
        # clip edges of our polygons would be rendered on the client side with 1px strokes.
        bounds_sql = self._getBoundingBoxSQL(0.01)

        query = """
          SELECT
            r.id,
            r.uid,
            r.type,
            r.name,
            parents.parents,
            ST_AsGeoJson(polygons.geometry, %d) AS geometry_geojson,
            ST_AsSVG(ST_Transform(ST_SetSRID(polygons.geometry, 4326), 900913)) AS geometry_mercator_svg
          FROM regions r
          INNER JOIN (
                      SELECT
                        region_id,
                        ST_Collect(geometry) AS geometry
                      FROM (
                            SELECT
                              region_id,
                              ST_Intersection(%s, polygon) AS geometry
                            FROM region_polygons_zoom%d
                            WHERE %f <= max_longitude
                              AND %f >= min_longitude
                              AND %f <= max_latitude
                              AND %f >= min_latitude
                              %s
                           ) x
                      WHERE GeometryType(geometry) IN ('POLYGON', 'MULTIPOLYGON')
                      GROUP BY region_id
                     ) polygons
                  ON r.id = polygons.region_id
          LEFT JOIN region_parents_strings parents
                  ON r.id = parents.region_id
          ORDER BY r.position
          """ % (
              self.tile.getFloatDecimalsForZoom() + (self.tile.zoom >= 15 and 1 or 0),
              bounds_sql, self.tile.coord.zoom,
              nw.lon, se.lon, se.lat, nw.lat,
              self.tile.coord.zoom >= 15 and '' or (
                'AND (area_in_m > %d OR (area_in_m > %d AND is_island IS TRUE))'
                % (self.tile.getMinAreaForZoom(), self.tile.getMinIslandAreaForZoom()))
              )

        return query

    def _getStatisticsSQL(self, region_ids):
        query = """
            SELECT
                v.region_id, i.name, v.year, i.value_type, v.value_integer, v.value_float, v.note
            FROM
                indicator_region_values v
            INNER JOIN indicators i ON v.indicator_id = i.id
            WHERE v.region_id IN (%s)
            """ % (','.join([ "'%s'" % region_id for region_id in region_ids ]))

        return query

    def _populateStatistics(self, tile_data, region_id_to_json_id):
        region_ids = region_id_to_json_id.keys()
        if len(region_ids) == 0: return

        if not self.include_statistics:
            for region_id, json_id in region_id_to_json_id.items():
                tile_data.addRegionStatistic(json_id, 0, 'TO-FILL', region_id, None)
        else:
            sql = self._getStatisticsSQL(region_ids)
            self.db_cursor.execute(sql)

            for row in self.db_cursor:
                region_id = row['region_id']
                json_id = region_id_to_json_id[region_id]

                name = row['name']
                year = row['year']
                value_type = row['value_type']
                value = None
                if value_type == 'integer':
                    value = row['value_integer']
                elif value_type == 'float':
                    value = row['value_float']
                else:
                    raise Exception('Unknown value type %r' % value_type)
                note = row['note']

                tile_data.addRegionStatistic(json_id, year, name, value, note)

    def getTileData(self):
        tile_data = TileData(render_utfgrid_for_tile=self.tile)

        sql = self._getPolygonsSQL()
        self.db_cursor.execute(sql)

        region_id_to_json_id = {}

        for row in self.db_cursor:
            region_id = row['id']

            parents = []
            if row['parents'] and len(row['parents']):
              parents = row['parents'].split(',')

            properties = {
                'name': row['name'],
                'type': row['type'],
                'uid': row['uid'],
                'parents': parents
            }

            json_id = properties['type'] + '-' + properties['uid']
            tile_data.addRegion(json_id, properties, row['geometry_geojson'], row['geometry_mercator_svg'])

            region_id_to_json_id[region_id] = json_id

        self._populateStatistics(tile_data, region_id_to_json_id)

        return tile_data
