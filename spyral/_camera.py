from __future__ import division
import spyral
import pygame
import math
from collections import defaultdict
import operator
import sys
import weakref

class _Blit(object):
    __slots__ = ['surface', 'rect', 'layer', 'flags', 'static', 'clipping']
    def __init__(self, surface, rect, layer, flags, static, clipping):
        self.surface = surface
        self.rect = rect
        self.layer = layer
        self.flags = flags
        self.static = static
        self.clipping = clipping
        

@spyral.memoize._ImageMemoize
def _scale(s, factor):
    if factor == (1.0, 1.0):
        return s
    size = s.get_size()
    new_size = (int(math.ceil(size[0] * factor[0])),
                int(math.ceil(size[1] * factor[1])))
    t = pygame.transform.smoothscale(s,
                               new_size,
                               pygame.Surface(new_size, pygame.SRCALPHA).convert_alpha())
    return t


class Camera(object):
    """
    Cameras should never be instantiated directly. Instead, you should
    call `make_camera` on the camera passed into your scene.
    """
    def __init__(self, virtual_size=None,
                 real_size=None,
                 root = False):
        if root:
            self._surface = pygame.display.get_surface()
            if real_size is None or real_size == (0, 0):
                self._rsize = self._surface.get_size()
            else:
                self._rsize = real_size
        elif real_size is None:
            raise ValueError("Must specify a real_size.")
        else:
            self._rsize = real_size

        if virtual_size is None or virtual_size == (0, 0):
            self._vsize = self._rsize
            self._scale = (1.0, 1.0)
        else:
            self._vsize = virtual_size
            self._scale = (self._rsize[0] / self._vsize[0],
                           self._rsize[1] / self._vsize[1])
        self._background = None
        self._root = root
                
        if self._root:
            self._background = pygame.surface.Surface(self._rsize)
            self._background.fill((255, 255, 255))
            self._surface.blit(self._background, (0, 0))
            self._blits = []
            self._dirty_rects = []
            self._clear_this_frame = []
            self._clear_next_frame = []
            self._soft_clear = []
            self._static_blits = {}
            self._rs = self
            self._rect = self._surface.get_rect()
            self._saved_blits = weakref.WeakKeyDictionary()
            self._backgrounds = weakref.WeakKeyDictionary()

    def make_child(self, virtual_size=None,
                   real_size=None):
        """
        Method for creating a new Camera.

        | *virtual_size* is a size of the virtual resolution to be used.
        | *real_size* is a size of the resolution with respect to the parent
          camera (not to the physical display, unless the parent camera is the
          root one). This allows multi-level nesting of cameras, if needed.
        """
        if real_size == (0, 0) or real_size is None:
            real_size = self.get_size()
        y = Camera(virtual_size, real_size, False)
        y._parent = self
        y._scale = spyral.Vec2D(self._scale) * y._scale
        y._rect = pygame.Rect((0, 0),
                              spyral.point.scale(real_size, self._scale))
        y._rs = self._rs
        return y

    def get_size(self):
        """
        Returns the virtual size of this camera's display.
        """
        return self._vsize

    def get_rect(self):
        """
        Returns a rect the virtual size of this camera's display
        """
        return spyral.Rect((0, 0), self._vsize)

    def set_background(self, image):
        """
        Sets a background for this camera's display.
        """
        surface = image._surf
        scene = spyral._get_executing_scene()
        if surface.get_size() != self._vsize:
            raise ValueError("Background size must match the display size.")
        if not self._root:
            if scene is spyral.director.get_scene():
                self._rs._background = surface
                self._rs._clear_this_frame.append(surface.get_rect())
            else:
                self._rs._backgrounds[scene] = surface

            
    def _blit(self, surface, position, layer, flags, clipping):
        position = spyral.point.scale(position, self._scale)
        new_surface = _scale(surface, self._scale)
        r = pygame.Rect(position, new_surface.get_size())

        if self._rect.contains(r):
            pass
        elif self._rect.colliderect(r):
            x = r.clip(self._rect)
            y = x.move(-r.left, -r.top)
            new_surface = new_surface.subsurface(y)
            r = x
        else:
            return

        self._rs._blits.append(_Blit(new_surface,
                                     r,
                                     layer,
                                     flags,
                                     False,
                                     clipping))

    def _static_blit(self, sprite, surface, position, layer, flags, clipping):
        position = spyral.point.scale(position, self._scale)
        rs = self._rs
        redraw = sprite in rs._static_blits
        if redraw:
            r2 = rs._static_blits[sprite][1]
        new_surface = _scale(surface, self._scale)
        r = pygame.Rect(position, new_surface.get_size())
        if self._rect.contains(r):
            pass
        elif self._rect.colliderect(r):
            x = r.clip(self._rect)
            y = x.move(-r.left, -r.top)
            new_surface = new_surface.subsurface(y)
            r = x
        else:
            return

        rs._static_blits[sprite] = _Blit(new_surface,
                                          r,
                                          layer,
                                          flags,
                                          True,
                                          clipping)
        if redraw:
            rs._clear_this_frame.append(r2.union(r))
        else:
            rs._clear_this_frame.append(r)

    def _remove_static_blit(self, sprite):
        if not self._root:
            self._rs._remove_static_blit(sprite)
            return
        try:
            x = self._static_blits.pop(sprite)
            self._clear_this_frame.append(x.rect)
        except:
            pass

    def _draw(self):
        """
        Called by the director at the end of every .render() call to do
        the actual drawing.
        """

        # This function sits in a potential hot loop
        # For that reason, some . lookups are optimized away
        if not self._root:
            return
        screen = self._surface

        # Let's finish up any rendering from the previous frame
        # First, we put the background over all blits
        x = self._background.get_rect()
        for i in self._clear_this_frame + self._soft_clear:
            i = x.clip(i)
            b = self._background.subsurface(i)
            screen.blit(b, i)

        # Now, we need to blit layers, while simultaneously re-blitting
        # any static blits which were obscured
        static_blits = len(self._static_blits)
        dynamic_blits = len(self._blits)
        blits = self._blits + list(self._static_blits.values())
        blits.sort(key=operator.attrgetter('layer'))
        
        # Clear this is a list of things which need to be cleared
        # on this frame and marked dirty on the next
        clear_this = self._clear_this_frame
        # Clear next is a list which will become clear_this on the next
        # draw cycle. We use this for non-static blits to say to clear
        # That spot on the next frame
        clear_next = self._clear_next_frame
        # Soft clear is a list of things which need to be cleared on
        # this frame, but unlike clear_this, they won't be cleared
        # on future frames. We use soft_clear to make things static
        # as they are drawn and then no longer cleared
        soft_clear = self._soft_clear
        self._soft_clear = []
        screen_rect = screen.get_rect()
        drawn_static = 0
        
        blit_flags_available = pygame.version.vernum < (1, 8)
        
        for blit in blits:
            blit_clipping_offset, blit_clipping_region = blit.clipping
            blit_rect = blit.rect.move(blit_clipping_offset)
            blit_flags = blit.flags if blit_flags_available else 0
            # If a blit is entirely off screen, we can ignore it altogether
            if not screen_rect.contains(blit_rect) and not screen_rect.colliderect(blit_rect):
                continue
            if blit.static:
                skip_soft_clear = False
                for rect in clear_this:
                    if blit_rect.colliderect(rect):
                        screen.blit(blit.surface, blit_rect, blit_clipping_region, blit_flags)
                        skip_soft_clear = True
                        clear_this.append(blit_rect)
                        self._soft_clear.append(blit_rect)
                        drawn_static += 1
                        break
                if skip_soft_clear:
                    continue
                for rect in soft_clear:
                    if blit_rect.colliderect(rect):
                        screen.blit(blit.surface, blit_rect, blit_clipping_region, blit_flags)
                        soft_clear.append(blit.rect)
                        drawn_static += 1
                        break
            else:                
                if screen_rect.contains(blit_rect):
                    r = screen.blit(blit.surface, blit_rect, blit_clipping_region, blit_flags)
                    clear_next.append(r)
                elif screen_rect.colliderect(blit_rect):
                    x = blit.rect.clip(screen_rect)
                    y = x.move(-blit_rect.left, -blit_rect.top)
                    b = blit.surf.subsurface(y)
                    r = screen.blit(blit.surface, blit_rect, blit_clipping_region, blit_flags)
                    clear_next.append(r)

        # print "%d / %d static drawn, %d dynamic" %
        #       (drawn_static, len(s), len(blits))
        pygame.display.set_caption("%d / %d static, %d dynamic. %d ups, %d fps" %
                                   (
                                   drawn_static, static_blits, dynamic_blits, spyral.director.get_scene(
                                       ).clock.ups,
                                   spyral.director.get_scene().clock.fps))
        # Do the display update
        pygame.display.update(self._clear_next_frame + self._clear_this_frame)
        # Get ready for the next call
        self._clear_this_frame = self._clear_next_frame
        self._clear_next_frame = []
        self._blits = []

    def _exit_scene(self, scene):
        self._saved_blits[scene] = self._static_blits
        self._static_blits = {}
        self._backgrounds[scene] = self._background
        self._background = pygame.surface.Surface(self._rsize)
        self._background.fill((255, 255, 255))
        self._surface.blit(self._background, (0, 0))

    def _enter_scene(self, scene):
        self._static_blits = self._saved_blits.pop(scene, self._static_blits)
        if scene in self._backgrounds:
            self._background = self._backgrounds.pop(scene)
            self._clear_this_frame.append(self._background.get_rect())

    def layers(self):
        """ Returns a list of this camera's layers. """
        return self._layers[:]

    def world_to_local(self, pos):
        """
        Converts coordinates from the display to coordinates in this camera's
        space. If the coordinate is outside, then it returns None.
        """
        pos = spyral.Vec2D(pos)
        if self._rect.collidepoint(pos):
            pos = pos / self._scale
            return pos
        return None

    def redraw(self):
        self._clear_this_frame.append(pygame.Rect((0,0), self._vsize))