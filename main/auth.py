from django.contrib.auth import get_user_model
from django.conf import settings
from django.contrib.auth.models import Permission
from django.db.models import Exists, OuterRef, Q
from django.contrib.auth.backends import ModelBackend

UserModel = get_user_model()

class AuthBackend(ModelBackend):
    def user_can_authenticate(self, user):
        """
        Reject users with is_active=False. Custom user models that don't have
        that attribute are allowed.
        """
        is_active = getattr(user, 'is_active', None)
        return is_active or is_active is None

    def authenticate(self, user_data, **kwargs):
        if not (kwargs['token'] and source):
            return None
        if source=='google':
            emailid, name = login_google(kwargs['token'])
            check_info = {'google_email':emailid}
            check_contact_info = {'contact_email':emailid}
            signup_info = {'google_email': emailid, 'name':name, 'contact_email':emailid}
            update_info = {'google_email': emailid, 'name':name}
        elif source=='fb':
            emailid, name = login_fb(kwargs['token'])
            check_info = {'fb_email':emailid}
            check_contact_info = {'contact_email':emailid}
            update_info = {'fb_email': emailid, 'name':name}
            signup_info = {'fb_email': emailid, 'name':name, 'contact_email':emailid}
            
        try:
            if None in check_info.values():
                return None
            user = UserModel.objects.get(**check_info)
        except UserModel.DoesNotExist:
            # Run the default password hasher once to reduce the timing
            # difference between an existing and a nonexistent user (#20760).
            try:
                user = UserModel.objects.get(**check_contact_info)
                user, created = UserModel.objects.update_or_create(
                    contact_email=check_contact_info['contact_email'],
                    defaults = update_info
                )
            except UserModel.DoesNotExist:
                user = UserModel.objects.create_user(**signup_info)
        if self.user_can_authenticate(user):
            return user

    def get_user(self, user_id):
        try:
            user = UserModel._default_manager.get(pk=user_id)
        except UserModel.DoesNotExist:
            return None
        return user if self.user_can_authenticate(user) else None
        
    def _get_user_permissions(self, user_obj):
        return user_obj.user_permissions.all()

    def _get_group_permissions(self, user_obj):
        user_groups_field = get_user_model()._meta.get_field('groups')
        user_groups_query = 'group__%s' % user_groups_field.related_query_name()
        return Permission.objects.filter(**{user_groups_query: user_obj})

    def _get_permissions(self, user_obj, obj, from_name):
        """
        Return the permissions of `user_obj` from `from_name`. `from_name` can
        be either "group" or "user" to return permissions from
        `_get_group_permissions` or `_get_user_permissions` respectively.
        """
        if not user_obj.is_active or user_obj.is_anonymous or obj is not None:
            return set()

        perm_cache_name = '_%s_perm_cache' % from_name
        if not hasattr(user_obj, perm_cache_name):
            if user_obj.is_superuser:
                perms = Permission.objects.all()
            else:
                perms = getattr(self, '_get_%s_permissions' % from_name)(user_obj)
            perms = perms.values_list('content_type__app_label', 'codename').order_by()
            setattr(user_obj, perm_cache_name, {"%s.%s" % (ct, name) for ct, name in perms})
        return getattr(user_obj, perm_cache_name)

    def get_user_permissions(self, user_obj, obj=None):
        """
        Return a set of permission strings the user `user_obj` has from their
        `user_permissions`.
        """
        return self._get_permissions(user_obj, obj, 'user')

    def get_group_permissions(self, user_obj, obj=None):
        """
        Return a set of permission strings the user `user_obj` has from the
        groups they belong.
        """
        return self._get_permissions(user_obj, obj, 'group')

    def get_all_permissions(self, user_obj, obj=None):
        if not user_obj.is_active or user_obj.is_anonymous or obj is not None:
            return set()
        if not hasattr(user_obj, '_perm_cache'):
            user_obj._perm_cache = {
                       *self.get_user_permissions(user_obj, obj=obj),
                       *self.get_group_permissions(user_obj, obj=obj),
            }
        return user_obj._perm_cache

    def has_perm(self, user_obj, perm, obj=None):
        return user_obj.is_active and perm in self.get_all_permissions(user_obj, obj=obj)

    def has_module_perms(self, user_obj, app_label):
        """
        Return True if user_obj has any permissions in the given app_label.
        """
        return user_obj.is_active and any(
            perm[:perm.index('.')] == app_label
            for perm in self.get_all_permissions(user_obj)
        )

    def with_perm(self, perm, is_active=True, include_superusers=True, obj=None):
        """
        Return users that have permission "perm". By default, filter out
        inactive users and include superusers.
        """
        if isinstance(perm, str):
            try:
                app_label, codename = perm.split('.')
            except ValueError:
                raise ValueError(
                    'Permission name should be in the form '
                    'app_label.permission_codename.'
                )
        elif not isinstance(perm, Permission):
            raise TypeError(
                'The `perm` argument must be a string or a permission instance.'
            )

        UserModel = get_user_model()
        if obj is not None:
            return UserModel._default_manager.none()

        permission_q = Q(group__user=OuterRef('pk')) | Q(user=OuterRef('pk'))
        if isinstance(perm, Permission):
            permission_q &= Q(pk=perm.pk)
        else:
            permission_q &= Q(codename=codename, content_type__app_label=app_label)

        user_q = Exists(Permission.objects.filter(permission_q))
        if include_superusers:
            user_q |= Q(is_superuser=True)
        if is_active is not None:
            user_q &= Q(is_active=is_active)

        return UserModel._default_manager.filter(user_q)