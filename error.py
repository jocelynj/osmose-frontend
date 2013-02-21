#! /usr/bin/env python
#-*- coding: utf-8 -*-
###########################################################################
##                                                                       ##
## Copyrights Etienne Chové <chove@crans.org> 2009                       ##
##                                                                       ##
## This program is free software: you can redistribute it and/or modify  ##
## it under the terms of the GNU General Public License as published by  ##
## the Free Software Foundation, either version 3 of the License, or     ##
## (at your option) any later version.                                   ##
##                                                                       ##
## This program is distributed in the hope that it will be useful,       ##
## but WITHOUT ANY WARRANTY; without even the implied warranty of        ##
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         ##
## GNU General Public License for more details.                          ##
##                                                                       ##
## You should have received a copy of the GNU General Public License     ##
## along with this program.  If not, see <http://www.gnu.org/licenses/>. ##
##                                                                       ##
###########################################################################

from bottle import route, request, template, response, abort
import StringIO, os, tempfile, urllib2

from tools import osmose_common
from tools import utils


def _get(db, err_id):
    columns_marker = ["marker.item", "marker.source", "marker.class", "marker.elems", "marker.subclass",
        "ROUND(marker.lat/1000000., 8) AS lat", "ROUND(marker.lon/1000000., 8) AS lon",
        "dynpoi_class.title", "marker.subtitle", "dynpoi_update_last.timestamp"]
    sql = "SELECT " + ",".join(columns_marker) + """
    FROM
        marker
        JOIN dynpoi_class ON
            marker.source = dynpoi_class.source AND
            marker.class = dynpoi_class.class
        JOIN dynpoi_update_last ON
            marker.source = dynpoi_update_last.source
    WHERE
        marker.id = %s
    """
    db.execute(sql, (err_id, ))
    marker = db.fetchone()

    if not marker:
        abort(410, "Id is not present in database.")

    columns_elements = ["elem_index", "data_type", "id", "tags", "username"]
    sql = "SELECT " + ",".join(columns_elements) + """
    FROM
        marker_elem
    WHERE
        marker_id = %s
    ORDER BY
        elem_index
    """
    db.execute(sql, (err_id, ))
    elements = db.fetchall()

    columns_fix = ["diff_index", "elem_data_type", "elem_id", "tags_create", "tags_modify", "tags_delete"]
    sql = "SELECT " + ",".join(columns_fix) + """
    FROM
        marker_fix
    WHERE
        marker_id = %s
    ORDER BY
        diff_index
    """
    db.execute(sql, (err_id, ))
    fix = db.fetchall()

    return (marker, columns_marker, elements, columns_elements, fix, columns_fix)


@route('/error/<err_id:int>')
def display(db, err_id):
    (marker, columns_marker, elements, columns_elements, fix, columns_fix) = _get(db, err_id)

    return template('error/index', err_id=err_id,
        marker=marker, columns_marker=columns_marker,
        elements=elements, columns_elements=columns_elements,
        fix=fix, columns_fix=columns_fix)


@route('/api/0.2/error/<err_id:int>')
def error(db, lang, err_id):
    data_type = { "N": "node", "W": "way", "R": "relation", "I": "infos"}

    # TRANSLATORS: link to tooltip help
    url_help = _("http://wiki.openstreetmap.org/wiki/Osmose/errors")

    translate = utils.translator(lang)

    (marker, columns_marker, elements, columns_elements, fixies, columns_fix) = _get(db, err_id)

    lat       = str(marker["lat"])
    lon       = str(marker["lon"])
    title     = translate.select(marker["title"])
    subtitle  = translate.select(marker["subtitle"])
    b_date    = marker["timestamp"] or ""
    item      = marker["item"] or 0

    def expand_tags(tags, links, short = False):
      t = []
      if short:
        for k in tags:
          t.append({"k": k})
      else:
        for (k, v) in tags.items():
          if links and links.has_key(k):
            t.append({"k": k, "v": v, "vlink": links[k]})
          else:
            t.append({"k": k, "v": v})
      return t

    elems = []
    for elem in elements:
      if elem["data_type"]:
        tags = elem["tags"]
        try:
            links = t2l.checkTags(tags)
        except:
            links = {}
        tmp_elem = {data_type[elem["data_type"]]: True,
                    "type": data_type[elem["data_type"]],
                    "id": elem["id"],
                    "tags": expand_tags(tags, links),
                    "fixes": [],
                   }
        for fix in fixies:
          if (fix["elem_data_type"] and
              fix["elem_data_type"] == elem["data_type"] and
              fix["elem_id"] == elem["id"]):
            tmp_elem["fixes"].append({"num": fix["diff_index"],
                                      "add": expand_tags(fix["tags_create"], {}),
                                      "mod": expand_tags(fix["tags_modify"], {}),
                                      "del": expand_tags(fix["tags_delete"], {}, True),
                                     })
        elems.append(tmp_elem)

    new_elems = []
    for fix in fixies:
        if fix["elem_data_type"]:
            found = False
            for e in elems:
                if (e["type"] == data_type[fix["elem_data_type"]] and
                    e["id"] == fix[ "elem_id"]):

                    found = True
                    break
            if not found:
                new_elems.append({"num": fix["diff_index"],
                                  "add": expand_tags(fix["tags_create"], {}),
                                  "mod": expand_tags(fix["tags_modify"], {}),
                                  "del": expand_tags(fix["tags_delete"], {}, True),
                                 })

    return {
        "lat":lat, "lon":lon,
        "minlat": float(lat) - 0.002, "maxlat": float(lat) + 0.002,
        "minlon": float(lon) - 0.002, "maxlon": float(lon) + 0.002,
        "error_id":err_id,
        "title":title, "subtitle":subtitle,
        "b_date":b_date.strftime("%Y-%m-%d"),
        "item":item,
        "elems":elems, "new_elems":new_elems,
        "elems_id":marker["elems"].replace("_",","),
        "url_help":url_help
    }


@route('/api/0.2/error/<err_id:int>/<status:re:(done|false)>')
def status(err_id, status):
    if osmose_common.remove_bug(err_id, status) == 0:
        abort(200, "OK")
    else:
        abort(410, "FAIL")


@route('/api/0.2/error/<err_id:int>/fix')
@route('/api/0.2/error/<err_id:int>/fix/<fix_num:int>')
def fix(db, err_id, fix_num=0):
    remote_url = "http://api.openstreetmap.fr/api/0.6"

    data_type = {"N": "node", "W": "way", "R": "relation"}

    columns = [ "diff_index", "elem_data_type", "elem_id", "tags_create", "tags_modify", "tags_delete" ]
    sql = "SELECT " + ", ".join(columns) + """
FROM marker_fix
WHERE marker_id = %s AND diff_index = %s
"""

    db.execute(sql, (err_id, fix_num))
    res = db.fetchone()
    if res:
        response.content_type = 'text/xml; charset=utf-8'
        if res["elem_id"] > 0:
            elem_url = os.path.join(remote_url,
                                    data_type[res["elem_data_type"]],
                                    str(res["elem_id"])
                                   )
            if res["elem_data_type"] == "W":
                elem_url = os.path.join(elem_url, "full")
            elem_io = urllib2.urlopen(elem_url)
            osm_read = OsmSax.OsmSaxReader(elem_io)
            out = StringIO.StringIO()
            o = OsmSaxFixWriter(out, "UTF-8",
                                res["elem_data_type"], res["elem_id"],
                                res["tags_create"], res["tags_modify"], res["tags_delete"])
            o.startDocument()
            osm_read.CopyTo(o)

            return out.getvalue()

        else:
            # create new object
            data = {}
            data["id"] = -1
            data["tag"] = {}
            for (k, v) in res["tags_create"].iteritems():
                data["tag"][k] = v
            sql = "SELECT lat, lon FROM marker WHERE id = %s"
            db.execute(sql, (err_id, ))
            res2 = db.fetchone()
            data["lat"] = res2["lat"] / 1000000.
            data["lon"] = res2["lon"] / 1000000.

            if res["elem_data_type"] == 'N':
                return OsmSax.NodeToXml(data, full=True)
            elif res["elem_data_type"] == 'W':
                return OsmSax.WayToXml(data, full=True)
            elif res["elem_data_type"] == 'R':
                return OsmSax.RelationToXml(data, full=True)

    else:
        abort(412, "Precondition Failed")
        #print "No error found"



from tools import OsmSax

class OsmSaxFixWriter(OsmSax.OsmSaxWriter):

    def __init__(self,
                 out, enc,
                 elem_type, elem_id,
                 tags_create, tags_modify, tags_delete):
        OsmSax.OsmSaxWriter.__init__(self, out, enc)

        self.elem_type = elem_type
        self.elem_id = elem_id
        self.tags_create = tags_create
        self.tags_modify = tags_modify
        self.tags_delete = tags_delete

    def fix_tags(self, data):
        for k in self.tags_delete:
            del data["tag"][k]
        for (k, v) in self.tags_create.items():
            data["tag"][k] = v
        for (k, v) in self.tags_modify.items():
            data["tag"][k] = v
        data["action"] = "modify"
        return data

    def NodeCreate(self, data):
        if self.elem_type == "N" and self.elem_id == data["id"]:
            data = self.fix_tags(data)
        OsmSax.OsmSaxWriter.NodeCreate(self, data)

    def WayCreate(self, data):
        if self.elem_type == "W" and self.elem_id == data["id"]:
            data = self.fix_tags(data)
        OsmSax.OsmSaxWriter.WayCreate(self, data)

    def RelationCreate(self, data):
        if self.elem_type == "R" and self.elem_id == data["id"]:
            data = self.fix_tags(data)
        OsmSax.OsmSaxWriter.RelationCreate(self, data)
