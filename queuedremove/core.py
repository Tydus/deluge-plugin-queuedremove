#
# core.py
#
# Copyright (C) 2013 Tydus <Tydus@Tydus.org>
#
# Basic plugin template created by:
# Copyright (C) 2008 Martijn Voncken <mvoncken@gmail.com>
# Copyright (C) 2007-2009 Andrew Resch <andrewresch@gmail.com>
# Copyright (C) 2009 Damien Churchill <damoxc@gmail.com>
#
# Deluge is free software.
#
# You may redistribute it and/or modify it under the terms of the
# GNU General Public License, as published by the Free Software
# Foundation; either version 3 of the License, or (at your option)
# any later version.
#
# deluge is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with deluge.    If not, write to:
# 	The Free Software Foundation, Inc.,
# 	51 Franklin Street, Fifth Floor
# 	Boston, MA  02110-1301, USA.
#
#    In addition, as a special exception, the copyright holders give
#    permission to link the code of portions of this program with the OpenSSL
#    library.
#    You must obey the GNU General Public License in all respects for all of
#    the code used other than OpenSSL. If you modify file(s) with this
#    exception, you may extend this exception to your version of the file(s),
#    but you are not obligated to do so. If you do not wish to do so, delete
#    this exception statement from your version. If you delete this exception
#    statement from all source files in the program, then also delete it here.
#

from deluge.log import LOG as log
from deluge.plugins.pluginbase import CorePluginBase
import deluge.component as _component
import deluge.configmanager
from deluge.core.rpcserver import export

DEFAULT_PREFS = {
    "remove_threshold": 104857600, # 100 MiB
    "stop_threshold": 1073741824, # 1 GiB
    "remove_queue": [] # [[torrent_id,...],[torrent_id,...],...]
}

# A Synactic Sugar to component.get()
class component(Object):
    def __getattribute__(self,attr):
        return _component.get(attr)

class Core(CorePluginBase):

    # Interfaces
    def enable(self):
        log.info("QueuedRemove plugin enabled")

        self.torrents = component.Core.torrentmanager.torrents
        self.config = deluge.configmanager.ConfigManager("queuedremove.conf", DEFAULT_PREFS)
        component.EventManager.register_event_handler("TorrentRemovedEvent", self.post_torrent_remove)

        # Remove queue, save to disk
        self.rq=self.config["rq"]
        # Remove priorities, only cached in memory
        self.remove_priorities={}

        # Register Torrent status field
        component.CorePluginManager.register_status_field("remove_priority", self.status_get_priority)

        # Start a timer to check and remove once a minute
        self.check_timer = LoopingCall(self.check_and_remove)
        self.check_timer.start(60)

    def disable(self):

        self.check_timer.stop()

        component.CorePluginManager.deregister_status_field("remove_priority")

        log.info("QueuedRemove plugin disabled")

    def update(self):
        pass

    @export
    def set_config(self, config):
        """Sets the config dictionary"""
        for key in config.keys():
            self.config[key] = config[key]
        self.config.save()

    @export
    def get_config(self):
        """Returns the config dictionary"""
        return self.config.config

    # Utilities
    def get_rp_groups(self, *tids):
        """Get Remove Priority Groups from tids"""
        ret=[]
        for i in tids:
            rp=self.remove_priorities.get(i)
            if not rp:
                self.warning("Torrent %s is not in the queue"%i)
                continue
            if rp not in ret:
                ret.append(rp)
        return ret

    def remove_invalid_torrent(self):
        """Remove invalid torrent from the remove queue"""
        for i,p in enumerate(self.rq):
            # Filter out invalid torrent_id
            self.rq[i]=filter(lambda x: x in self.torrents, p)

        # Filter out empty priority
        self.rq=filter(lambda x: x!=[], self.rq)

    def update_remove_priorities(self):
        """Update remove_priorities list"""
        rp={}
        for i,p in enumerate(self.rq):
            for j in p:
                rp[j]=i

        self.remove_priorities=rp

    def apply_queue_change(self):
        """Apply a queue change"""
        self.remove_invalid_torrent()
        self.update_remove_priorities()
        return self.config.save()

    # Exports
    @export
    def add(self, *tids, ascend=True):
        """
        Add torrent(s) into the queue
        if ascend is True, then the torrents will be assign ascending priorities
        if False, the torrents will be assigned same priority
        """

        # Don't worry about empty priority, 
        # because this will be corrected during apply_queue_change()
        self.rq.append([])
        for i in tids:
            if self.remove_priorities.get(i):
                self.warning("Torrent %s already in queue with priority %d"%(
                    i,self.remove_priorities[i]
                ))
                continue
            if ascend:
                # Add to a new priority
                self.rq.append([i])
            else:
                # Add to the last priority
                self.rq[-1].append(i)

        return self.apply_queue_change()

    @export
    def remove(self, *tids):
        """Remove torrent(s) from the queue"""
        for i in tids:
            rp=self.remove_priorities.get(i)
            if not rp:
                self.warning("Torrent %s is not in the queue"%i)
                continue
            # Remove the torrent from queue
            self.rq[rp].remove(i)

        return self.apply_queue_change()

    @export
    def queue_top(self, *tids):
        """Move torrent(s) and all the same torrents in the same priority to top of the queue"""
        for i in reversed(get_rp_groups(*tids)):
            tmp=self.rq[i]
            del self.rq[i]
            self.rq.insert(tmp)

        return self.apply_queue_change()

    @export
    def queue_bottom(self, *tids):
        """Move torrent(s) and all the same torrents in the same priority to bottom of the queue"""
        for i in get_rp_groups(*tids):
            tmp=self.rq[i]
            del self.rq[i]
            self.rq.append(tmp)

        return self.apply_queue_change()

    @export
    def queue_forward(self, *tids):
        """Move torrent(s) forward in the queue"""
        for i in sorted(get_rp_groups(*tids)):
            # Filter out the first one
            if i!=0:
                # Swap with the one before it
                self.rq[i-1],self.rq[i]=self.rq[i],self.rq[i-1]

        return self.apply_queue_change()

    @export
    def queue_back(self, *tids):
        """Move torrent(s) back in the queue"""
        for i in reversed(sorted(get_rp_groups(*tids))):
            # Filter out the last one
            if i!=len(self.rq):
                # Swap with the one after it
                self.rq[i],self.rq[i+1]=self.rq[i+1],self.rq[i]

        return self.apply_queue_change()

    @export
    def queue_set(self, *tids, pos):
        """Force set torrent's(s') queue position"""
        for i in tids:
            rp=self.remove_priorities.get(i)
            if not rp:
                self.warning("Torrent %s is not in the queue"%i)
                continue
            self.rq[rp].remove(i)

        # Put all of tid into self.rq[pos]
        # Don't need to validate because apply_queue_change() will do it
        if pos<0:
            self.rq.insert(0,tids)
        elif pos>len(self.rq):
            self.rq.append(tids)
        else:
            self.rq[pos]+=tids

        return self.apply_queue_change()

    # Triggers
    def status_get_priority(self,tid):
        return self.remove_priorities.get(tid,"")

    def post_torrent_remove(self,tid):
        """Trigger after remove a torrent"""
        log.debug("post_torrent_remove")
        self.remove(tid)

    def check_and_remove(self):
        """Check if needed and do remove from the queue"""
        log.debug("Checking remaining disk space")
        free_space=component.Core.core.get_free_space()

        log.debug("Free disk space: %s bytes"%free_space)

        # Check if disk space is above the remove threshold
        if free_space>self.config["remove_threshold"]:
            log.debug("The disk space is above the threshold, do nothing")
            return True

        log.info("The disk space is below the threshold, remove torrents in the queue")
        
        # Check if the queue is empty
        if self.rq=[]:
            log.warning("The queue is empty, abort")
            return False

        # Attention: don't use remaining disk space here due to disk space recycle latency
        # FIXME: if some torrents share spaces, 
        #        then the space actually recycled will be less than we calculated, 
        #        but it's OK at most times because we will come back 1 minute later.
        total_freed=0
        while total_freed<self.config["stop_threshold"]:
            rq_top=self.rq[0]
            # Remove all torrents in the cell
            for i in self.rq[0]
                # this value is an upper bound of the space we freed
                total_freed+=self.torrents[i].get_status("total_wanted_done")["total_wanted_done"]
                component.TorrentManager.remove(i, remove_date=True)
            del self.rq[0]

        return self.apply_queue_change()

