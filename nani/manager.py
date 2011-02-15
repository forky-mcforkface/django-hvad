from django.db import models
from django.db.models.query import QuerySet
from django.db.models.query_utils import Q
from django.utils.translation import get_language
from nani.utils import R, combine

class FieldTranslator(dict):
    """
    Translates *shared* field names from '<shared_field>' to
    'master__<shared_field>' and caches those names.
    """
    def __init__(self, manager):
        self.manager = manager
        self.shared_fields = tuple(self.manager.shared_model._meta.get_all_field_names()) + ('pk',)
        self.translated_fields  = tuple(self.manager.model._meta.get_all_field_names())
        super(FieldTranslator, self).__init__()
        
    def get(self, key):
        if not key in self:
            self[key] = self.build(key)
        return self[key]
    
    def build(self, key):
        if key.startswith(self.shared_fields):
            return 'master__%s' % key
        else:
            return key

        
class TranslationMixin(QuerySet):
    def __init__(self, model=None, query=None, using=None, real=None):
        self._local_field_names = None
        self._field_translator = None
        self._real_manager = real
        self._language_code = None
        super(TranslationMixin, self).__init__(model=model, query=query, using=using)

    #===========================================================================
    # Helpers and properties (ITNERNAL!)
    #===========================================================================

    @property
    def translations_manager(self):
        """
        Get the (real) manager of translations model
        """
        return self.model.objects
    
    @property
    def shared_model(self):
        """
        Get the shared model class
        """
        return self._real_manager.model
        
    @property
    def field_translator(self):
        """
        Field translator for this manager
        """
        if self._field_translator is None:
            self._field_translator = FieldTranslator(self)
        return self._field_translator
        
    @property
    def shared_local_field_names(self):
        if self._local_field_names is None:
            self._local_field_names = self.shared_model._meta.get_all_field_names()
        return self._local_field_names
    
    def _translate(self, *args, **kwargs):
        # Translated kwargs from '<shared_field>' to 'master__<shared_field>'
        # where necessary.
        newkwargs = {}
        for key, value in kwargs.items():
            newkwargs[self.field_translator.get(key)] = value
        # Translate args (Q objects) from '<shared_field>' to
        # 'master_<shared_field>' where necessary.
        newargs = []
        for q in args:
            newargs.append(self._recurse_q(q))
        return newargs, newkwargs
    
    def _translate_fieldnames(self, fieldnames):
        newnames = []
        for name in fieldnames:
            newnames.append(self.field_translator.get(name))
        return newnames        

    def _recurse_q(self, q):
        """
        Recursively translate fieldnames in a Q object.
        
        TODO: What happens if we span multiple relations?
        """
        newchildren =  []
        for child in q.children:
            if isinstance(child, R):
                newchildren.append(child)
            elif isinstance(child, Q):
                newq = self._recurse_q(child)
                newchildren.append(self._recurse_q(newq))
            else:
                key, value = child
                newchildren.append((self.field_translator.get(key), value))
        q.children = newchildren
        return q
    
    #===========================================================================
    # Queryset/Manager API 
    #===========================================================================
    
    def language(self, language_code=None):
        if not language_code:
            language_code = get_language()
        self._language_code = language_code
        return self.filter(language_code=language_code)
        
    def create(self, **kwargs):
        """
        When we create an instance, what we actually need to do is create two
        separate instances: One shared, and one translated.
        For this, we split the 'kwargs' into translated and shared kwargs
        and set the 'master' FK from in the translated kwargs to the shared
        instance.
        If 'language_code' is not given in kwargs, set it to the current
        language.
        """
        tkwargs = {}
        for key in kwargs.keys():
            if not key in self.shared_local_field_names:
                tkwargs[key] = kwargs.pop(key)
        # enforce a language_code
        if 'language_code' not in tkwargs:
            if self._language_code:
                tkwargs['language_code'] = self._language_code
            else:
                tkwargs['language_code'] = get_language()
        # Allow a pre-existing master to be passed, but only if no shared fields
        # are given.
        if 'master' in tkwargs:
            if kwargs:
                raise RuntimeError(
                    "Cannot explicitly use a master (shared) instance and shared fields in create"
                )
        else:
            # create shared instance
            shared = self._real_manager.create(**kwargs)
            tkwargs['master'] = shared
        # create translated instance
        trans = self.translations_manager.create(**tkwargs)
        # return combined instance
        return combine(trans)
    
    def get(self, *args, **kwargs):
        """
        Get an object by querying the translations model and returning a 
        combined instance.
        """
        # Enforce a language_code to be used
        newargs, newkwargs = self._translate(*args, **kwargs)
        # Enforce 'select related' onto 'master'
        qs = self.select_related('master')
        # Get the translated instance
        found = False
        if 'language_code' in newkwargs:
            language_code = newkwargs.pop('language_code')
            qs = qs.language(language_code)
        else:
            for where in qs.query.where.children:
                if where.children:
                    for child in where.children:
                        if child[0].field.name == 'language_code':
                            found = True
            if not found:
                qs = qs.language()
        trans = QuerySet.get(qs, *newargs, **newkwargs)
        # Return a combined instance
        return combine(trans)

    def filter(self, *args, **kwargs):
        newargs, newkwargs = self._translate(*args, **kwargs)
        return super(TranslationMixin, self).filter(*newargs, **newkwargs)

    def aggregate(self, *args, **kwargs):
        raise NotImplementedError()

    def latest(self, field_name=None):
        raise NotImplementedError()

    def in_bulk(self, id_list):
        raise NotImplementedError()

    def delete(self):
        raise NotImplementedError()
    delete.alters_data = True

    def update(self, **kwargs):
        raise NotImplementedError()
    update.alters_data = True

    def values(self, *fields):
        raise NotImplementedError()

    def values_list(self, *fields, **kwargs):
        raise NotImplementedError()

    def dates(self, field_name, kind, order='ASC'):
        raise NotImplementedError()

    def exclude(self, *args, **kwargs):
        raise NotImplementedError()

    def complex_filter(self, filter_obj):
        raise NotImplementedError()

    def annotate(self, *args, **kwargs):
        raise NotImplementedError()

    def order_by(self, *field_names):
        """
        Returns a new QuerySet instance with the ordering changed.
        """
        fieldnames = self._translate_fieldnames(field_names)
        return super(TranslationMixin, self).order_by(*fieldnames)
    
    def reverse(self):
        raise NotImplementedError()

    def defer(self, *fields):
        raise NotImplementedError()

    def only(self, *fields):
        raise NotImplementedError()
    
    def _clone(self, klass=None, setup=False, **kwargs):
        cloned = super(TranslationMixin, self)._clone(self.__class__, setup, **kwargs)
        cloned._local_field_names = self._local_field_names
        cloned._field_translator = self._field_translator
        cloned._language_code = self._language_code 
        cloned._real_manager = self._real_manager
        return cloned
    
    def __getitem__(self, item):
        return super(TranslationMixin, self).__getitem__(item)
    
    def __iter__(self):
        for obj in super(TranslationMixin, self).__iter__():
            yield combine(obj)


class TranslationManager(models.Manager):
    """
    Manager class for models with translated fields
    """
    def language(self, language_code=None):
        return self.get_query_set().language(language_code)
    
    @property
    def translations_model(self):
        """
        Get the translations model class
        """
        return self.model._meta.translations_model

    def get_query_set(self):
        """
        Make sure that querysets inherit the methods on this manager (chaining)
        """
        return TranslationMixin(self.translations_model, using=self.db, real=self._real_manager)
    
    def contribute_to_class(self, model, name):
        super(TranslationManager, self).contribute_to_class(model, name)
        self._real_manager = models.Manager()
        self._real_manager.contribute_to_class(self.model, '_%s' % name)