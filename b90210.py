#!/usr/bin/env python
#
# Copyright 2010 Hareesh Nagarajan

from django.utils import simplejson
from google.appengine.ext import webapp
from google.appengine.ext.webapp import template
from google.appengine.ext import db
from google.appengine.api import memcache

import StringIO
import calendar
import datetime
import logging
import os
import random
import time
import traceback
import twitter
import urllib2
import wsgiref.handlers

IGNORE =  [ 'apiwiki.twitter.com',
            'twitterfeed.com',
            'rss2twitter.com',
            'skygrid.com',
            'assetize.com',
            'Twitter Tools',
            'wp-to-twitter',
            'pivotallabs.com',
            '/devices',
            'bit.ly',
            'allyourtweet.com'
            'wordpress',
            'alexking.org',
            'bravenewcode.com',
            'tweethopper.com',
            ]

class SearchObject(db.Model):
  text = db.StringProperty(multiline=True)
  profile_image_url = db.StringProperty()
  from_user = db.StringProperty()
  source = db.StringProperty()
  tweet_id = db.IntegerProperty(indexed=True)
  created_at = db.DateTimeProperty(indexed=True)

class RTLinkObject(db.Model):
  text = db.StringProperty(multiline=True)
  profile_image_url = db.StringProperty()
  from_user = db.StringProperty()
  source = db.StringProperty()
  tweet_id = db.IntegerProperty(indexed=True)
  created_at = db.DateTimeProperty(indexed=True)

class HashtagObject(db.Model):
  tag = db.StringProperty()
  views = db.IntegerProperty(default=1)
  date = db.DateTimeProperty(auto_now_add=True)

class LinkObject(db.Model):
  tag = db.StringProperty()
  views = db.IntegerProperty(default=1)
  date = db.DateTimeProperty(auto_now_add=True)

class TwitterSearch:
  def __init__(self, query, lang='en'):
    self.url = 'http://search.twitter.com/search.json?q=%s&rpp=100' % query
    self.lang = lang

  def search(self):
    try:
      handle = urllib2.urlopen(self.url)
      data = simplejson.loads(handle.read())
      results = data['results']
      self.process_results(results)
    except Exception, e:
      s = StringIO.StringIO()
      traceback.print_exc(file=s)
      logging.info('Oops: %s', s.getvalue())

  def ignore_result(self, result):
    if result.has_key('iso_language_code'):
      if result['iso_language_code'] != self.lang:
        return True

    for ign in IGNORE:
      if ign in result['source']:
        return True

    return False

  def no_rt_or_link(self, result):
    if (result['text'].find('http://') == -1 and
        result['text'].find('RT ') == -1):
      return True
    return False

  def extract_source(self, source):
    idx = source.find('http://') + len('http://')
    length1 = source[idx:].find('/')
    length2 = source[idx:].find('&')
    length = length1
    if length2 != -1 and length2 < length1:
      length = length2
    just_source = source[idx:idx + length2].replace('www.', '')
    just_source = just_source.split('/')[0]
    return just_source

  def get_date(self, created_at):
    ts = calendar.timegm(time.strptime(created_at,
                                       '%a, %d %b %Y %H:%M:%S +0000'))
    return datetime.datetime.fromtimestamp(ts)

  def tweet_exist(self, tweet_id):
    result_list = db.GqlQuery(
      "SELECT * FROM SearchObject WHERE tweet_id = :tid "
      "ORDER BY tweet_id DESC LIMIT 1000",
      tid=tweet_id)
    for result in result_list:
      return True

    result_list = db.GqlQuery(
      "SELECT * FROM RTLinkObject WHERE tweet_id = :tid "
      "ORDER BY tweet_id DESC LIMIT 1000",
      tid=tweet_id)
    for result in result_list:
      return True

    return False

  def process_results(self, results):
    regular_idx = 0
    rt_idx = 0
    ignore = 0
    exists = 0
    total = 0
    to_write = []
    for result in results:
      total += 1
      if self.ignore_result(result):
        ignore += 1
        continue
      
      if self.tweet_exist(result['id']):
        exists += 1
        continue
        
      write_obj = None
      if self.no_rt_or_link(result):
        write_obj = SearchObject()
        regular_idx += 1
      else:
        write_obj = RTLinkObject()
        rt_idx += 1

      write_obj.tweet_id = result['id']
      write_obj.created_at = self.get_date(result['created_at'])
      write_obj.text = result['text']
      write_obj.profile_image_url = result['profile_image_url']
      write_obj.from_user = result['from_user']
      write_obj.source = self.extract_source(result['source'])
      to_write.append(write_obj)

    # Write it all in 1 shot.
    db.put(to_write)
          
    logging.info('total: %d wrote [search: %d, rt: %d] ignored: %d, exists: %d' %
                 (total, regular_idx, rt_idx, ignore, exists))


def NO_RT_OR_LINK(result):
  if (result.text.find('http://') == -1 and
      result.text.find('RT ') == -1):
    return True
  return False

def format_text(text):
  tokens = text.split()
  formatted = []
  for token in tokens:
    if token.find('@') == 0:
      at = '@<a href="http://twitter.com/%s">%s</a>' % (token[1:], token[1:])
      formatted.append(at)
    elif token.find('#') == 0:
      hashtag = '<a href="http://search.twitter.com/search?q=%%23%s">%s</a>' % (token[1:], token)
      formatted.append('%s' % hashtag)
    else:
      formatted.append(token)
  return ' '.join(formatted)

class PrettySearchObject():
  def __init__(self, text, from_user, created_at, profile_image_url):
    self.text = text
    self.from_user = from_user
    self.created_at = created_at
    self.profile_image_url = profile_image_url

class Home(webapp.RequestHandler):
  def get(self):
    PAGESIZE=20
    next_tweet_id = None
    next_bookmark = self.request.get("next")
    if next_bookmark:
      try:
        next_bookmark = int(next_bookmark)
      except ValueError, e:
        self.redirect('/')
        return
      result_list = SearchObject.all().order("-tweet_id").filter('tweet_id <=', int(next_bookmark)).fetch(PAGESIZE+1)
    else:
      result_list = SearchObject.all().order("-tweet_id").fetch(PAGESIZE+1)
      
    if len(result_list) == PAGESIZE+1:
      next_tweet_id = result_list[-1].tweet_id
      result_list = result_list[:PAGESIZE]

    new_result_list = []
    for rr in result_list:
      new_result_list.append(
        PrettySearchObject(format_text(rr.text),
                           rr.from_user,
                           rr.created_at,
                           rr.profile_image_url))

    template_values = {
      'result_list' : new_result_list,
      'next_tweet_id' : next_tweet_id,
      }

    path = os.path.join(os.path.dirname(__file__), 'templates/index.html')
    self.response.out.write(template.render(path, template_values))

class Cron(webapp.RequestHandler):
  def get(self):
    ts = TwitterSearch('cancer')
    ts.search()

class Sources(webapp.RequestHandler):
  def get(self):
    src_page = memcache.get("sources_page")
    if src_page:
      logging.info('sources hit memcache')
      self.response.out.write(src_page)
      return
    
    PAGESIZE=1000
    sources = {}

    next_tweet_id = None
    ret = True
    total = 0
    while ret:
      if next_tweet_id:
        result_list = SearchObject.all().order("-tweet_id").filter('tweet_id <=', next_tweet_id).fetch(PAGESIZE+1)
      else:
        result_list = SearchObject.all().order("-tweet_id").fetch(PAGESIZE+1)
        
      if len(result_list) == PAGESIZE+1:
        next_tweet_id = result_list[-1].tweet_id
        result_list = result_list[:PAGESIZE]
      else:
        ret = False
        logging.info('Hit %d', len(result_list))

      total += len(result_list)
      for rr in result_list:
        if rr.source in sources:
          sources[rr.source]+=1
        else:
          sources[rr.source]=1


    ll=[]

    alist = sorted(sources.iteritems(), key=lambda (k,v): (v, k), reverse=True)
    for l in alist:
      src = l[0]
      cnt = l[1]
      percent = (cnt*100.0)/total
      ll.append('%s %d (%0.2f%%)' % (src, cnt, percent))

    template_values = {
      'sources' : ll,
      'total' : total,
      }

    path = os.path.join(os.path.dirname(__file__), 'templates/sources.html')
    src_page = template.render(path, template_values)
    memcache.add("sources_page", src_page, 86400)
    self.response.out.write(src_page)

def main():
  wsgiref.handlers.CGIHandler().run(webapp.WSGIApplication([
    ('/', Home),
    ('/cron', Cron),
    ('/sources', Sources),
    ]))
   
if __name__ == '__main__':
  main()
