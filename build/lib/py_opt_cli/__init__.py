import requests
from requests.exceptions import HTTPError
from requests.utils import parse_header_links
import attr
import yaml
import click
from pathlib import Path
import difflib
import json
import logging


LOG = logging.getLogger(__name__)


META_FILE = '.meta.yaml'
SERIALIZER = 'serializer'

READ_ONLY = 'read_only'


def modifiable(field, value):
    return not field.metadata.get(READ_ONLY, False) and value is not None


@attr.s
class LazyCollection():
    optimizely = attr.ib()
    cls = attr.ib()
    endpoint = attr.ib()
    params = attr.ib(default=None)
    _items = attr.ib(default=None, init=False)

    def items(self):
        if self._items is None:
            doc_type = self.endpoint.rstrip('s')
            self._items = {}
            params = {
                'per_page': 50,
            }
            if self.params is not None:
                params.update(self.params)
            response = self.optimizely.session.get(
                'https://api.optimizely.com/v2/{}'.format(self.endpoint),
                params=params,
            )
            while True:
                response.raise_for_status()

                for doc in response.json():
                    doc_name = doc.get('name', '')
                    doc_id = doc.get('id', '')
                    LOG.debug(f'Parsing {doc_type}: {doc_name} ({doc_id})')
                    try:
                        obj = self.cls(**doc)
                    except TypeError:
                        LOG.exception(
                            'Error fetching %s %s (%s) from Optimizely:\n%s',
                            doc_type,
                            doc_name,
                            str(doc_id),
                            json.dumps(doc, indent=2)
                        )
                    else:
                        self._items[obj.id] = obj

                link_header = response.headers.get('link')
                next_url = None
                if link_header is not None:
                    for link in parse_header_links(link_header):
                        if link['rel'] in ('next', 'last'):
                            next_url = link['url']
                            break

                if next_url is not None:
                    response = self.optimizely.session.get(next_url)
                else:
                    break

        return self._items.items()

    def values(self):
        for _, value in self.items():
            yield value

    def __iter__(self):
        for key, _ in self.items():
            yield key

    def __getitem__(self, key):
        if self._items is not None and key in self._items:
            return self._items[key]

        response = self.optimizely.session.get(
            'https://api.optimizely.com/v2/{}/{}'.format(self.endpoint, key),
        )
        self.optimizely.raise_for_status(response)
        return self.cls(**response.json())

    def __setitem__(self, key, value):
        changes = attr.asdict(value, filter=modifiable)
        response = self.optimizely.session.patch(
            'https://api.optimizely.com/v2/{}/{}'.format(self.endpoint, key),
            json=changes,
        )
        self.optimizely.raise_for_status(response)


class Optimizely():
    def __init__(self, token):
        self.session = requests.Session()
        self.session.headers['Authorization'] = "Bearer {}".format(token)

    def raise_for_status(self, response):
        if not response.ok:
            raise HTTPError(response.json().get('message', response.reason), response=response)

    def projects(self):
        return LazyCollection(self, Project, 'projects')

    def experiments(self, project_id=None):
        if project_id:
            params = {
                'project_id': project_id,
            }
        else:
            params = None
        return LazyCollection(self, Experiment, 'experiments', params)

    def audiences(self, project_id=None):
        if project_id:
            params = {
                'project_id': project_id,
            }
        else:
            params = None
        return LazyCollection(self, Audience, 'audiences', params)

    def pages(self, project_id=None):
        if project_id:
            params = {
                'project_id': project_id,
            }
        else:
            params = None
        return LazyCollection(self, Page, 'pages', params)

    def events(self, project_id=None):
        if project_id:
            params = {
                'project_id': project_id,
                'include_classic': 'true',
            }
        else:
            params = None
        return LazyCollection(self, Event, 'events', params)


COLLECTION_CLS = 'collection_cls'


def subdocuments(cls, metadata=None):
    _metadata = {
        COLLECTION_CLS: cls,
    }
    if metadata is not None:
        _metadata.update(metadata)

    return attr.ib(
        convert=lambda docs: [cls(**doc) for doc in docs],
        metadata=_metadata,
    )


SUBDOCUMENT_CLS = 'subdocument_cls'


def subdocument(cls, metadata=None):
    _metadata = {
        SUBDOCUMENT_CLS: cls,
    }
    if metadata is not None:
        _metadata.update(metadata)

    return attr.ib(
        convert=lambda doc: cls(**doc),
        metadata=_metadata,
    )


class OptimizelyDocument(object):

    @classmethod
    def read_from_disk(cls, root):
        meta = read_meta_file(root)

        for field in attr.fields(cls):
            if COLLECTION_CLS in field.metadata:
                docs = []
                subdir = root / field.name
                if subdir.exists():
                    for dirname in meta.get(field.name):
                        docdir = subdir / dirname
                        if not docdir.is_dir():
                            continue
                        change = field.metadata[COLLECTION_CLS].read_from_disk(docdir)
                        docs.append(as_non_null_dict(change))
                meta[field.name] = docs
            elif SUBDOCUMENT_CLS in field.metadata:
                subdir = root / field.name
                if subdir.is_dir():
                    obj = field.metadata[SUBDOCUMENT_CLS].read_from_disk(subdir)
                    meta[field.name] = as_non_null_dict(obj)

        obj = cls(**meta)

        for field in attr.fields(cls):
            if SERIALIZER in field.metadata:
                serializer = field.metadata[SERIALIZER](root, obj, field.name)
                setattr(obj, field.name, serializer.read_from_disk())

        return obj

    def write_to_disk(self, root):
        if self.dirname is not None:
            docroot = root / self.dirname
        else:
            docroot = root
        docroot.mkdir(parents=True, exist_ok=True)

        meta = as_non_null_dict(self)

        for field in attr.fields(self.__class__):
            if COLLECTION_CLS in field.metadata:
                objs = []
                for obj in getattr(self, field.name):
                    obj.write_to_disk(docroot / field.name)
                    objs.append(obj.dirname)
                meta[field.name] = objs
            elif SUBDOCUMENT_CLS in field.metadata:
                obj = getattr(self, field.name)
                obj.write_to_disk(docroot / field.name)
                meta.pop(field.name, None)
            elif SERIALIZER in field.metadata:
                serializer = field.metadata[SERIALIZER](docroot, self, field.name)
                serializer.write_to_disk()
                meta.pop(field.name, None)

        write_meta_file(docroot, meta)

    @property
    def dirname(self):
        return slugify("{} {}".format(self.name, self.id))


@attr.s
class ConditionSerializer(object):
    root = attr.ib()
    obj = attr.ib()
    fieldname = attr.ib()

    @property
    def filename(self):
        return self.root / '{}.json'.format(self.fieldname)

    def read_from_disk(self):
        with self.filename.open() as field_file:
            try:
                return json.dumps(json.load(field_file))
            except json.JSONDecodeError:
                # the value can be simply "everyone" or a JSON blob
                return field_file.read()

    def write_to_disk(self):
        data = getattr(self.obj, self.fieldname)
        if data is not None:
            with self.filename.open('w') as field_file:
                try:
                    json.dump(
                        json.loads(data),
                        fp=field_file,
                        indent=2,
                    )
                except json.JSONDecodeError:
                    # the value can be simply "everyone" or a JSON blob
                    field_file.write(data)


@attr.s
class StaticContentSerializer(object):
    root = attr.ib()
    obj = attr.ib()
    fieldname = attr.ib()

    @property
    def filename(self):
        extension = None
        if hasattr(self.obj, 'type'):
            if self.obj.type == 'custom_css':
                extension = 'css'
            elif self.obj.type == 'custom_code':
                extension = 'js'
            elif self.obj.type in ('insert_html', 'insert_image'):
                extension = 'html'
        else:
            if self.fieldname in ('activation_code', 'project_javascript'):
                extension = 'js'
        if extension is None:
            extension = 'txt'
        return self.root / '{}.{}'.format(self.fieldname, extension)

    def read_from_disk(self):
        with self.filename.open() as field_file:
            return field_file.read()

    def write_to_disk(self):
        data = getattr(self.obj, self.fieldname)
        if data is not None:
            with self.filename.open('w') as field_file:
                field_file.write(data)


@attr.s
class WebSnippet(OptimizelyDocument):
    code_revision = attr.ib(metadata={READ_ONLY: True})
    enable_force_variation = attr.ib()
    exclude_disabled_experiments = attr.ib()
    exclude_names = attr.ib()
    include_jquery = attr.ib()
    ip_anonymization = attr.ib()
    js_file_size = attr.ib(metadata={READ_ONLY: True})
    library = attr.ib()
    ip_filter = attr.ib(default=None)
    project_javascript = attr.ib(default=None, metadata={SERIALIZER: StaticContentSerializer})

    @property
    def dirname(self):
        return None


@attr.s
class Project(OptimizelyDocument):
    name = attr.ib()
    confidence_threshold = attr.ib()
    platform = attr.ib()
    sdks = attr.ib()
    status = attr.ib(metadata={READ_ONLY: True})
    account_id = attr.ib(metadata={READ_ONLY: True})
    created = attr.ib(metadata={READ_ONLY: True})
    id = attr.ib()
    is_classic = attr.ib(metadata={READ_ONLY: True})
    last_modified = attr.ib(metadata={READ_ONLY: True})
    web_snippet = subdocument(WebSnippet)
    socket_token = attr.ib(default=None, metadata={READ_ONLY: True})
    dcp_service_id = attr.ib(default=None)


@attr.s
class Audience(OptimizelyDocument):
    project_id = attr.ib(metadata={READ_ONLY: True})
    archived = attr.ib()
    conditions = attr.ib(metadata={SERIALIZER: ConditionSerializer})
    description = attr.ib()
    is_classic = attr.ib(metadata={READ_ONLY: True})
    name = attr.ib()
    segmentation = attr.ib()
    created = attr.ib(metadata={READ_ONLY: True})
    id = attr.ib()
    last_modified = attr.ib(metadata={READ_ONLY: True})


@attr.s
class Page(OptimizelyDocument):
    edit_url = attr.ib()
    name = attr.ib()
    project_id = attr.ib(metadata={READ_ONLY: True})
    archived = attr.ib()
    category = attr.ib()
    key = attr.ib()
    created = attr.ib(metadata={READ_ONLY: True})
    id = attr.ib()
    last_modified = attr.ib(metadata={READ_ONLY: True})
    conditions = attr.ib(default=None, metadata={SERIALIZER: ConditionSerializer})
    activation_code = attr.ib(default=None, metadata={SERIALIZER: StaticContentSerializer})
    activation_type = attr.ib(default=None)
    page_type = attr.ib(default=None)


@attr.s
class Event(OptimizelyDocument):
    archived = attr.ib()
    category = attr.ib()
    event_type = attr.ib()
    name = attr.ib()
    project_id = attr.ib(metadata={READ_ONLY: True})
    created = attr.ib(metadata={READ_ONLY: True})
    id = attr.ib(metadata={READ_ONLY: True})
    is_classic = attr.ib(metadata={READ_ONLY: True})
    config = attr.ib(default=None)
    description = attr.ib(default=None)
    is_editable = attr.ib(metadata={READ_ONLY: True}, default=None)
    key = attr.ib(default=None)
    page_id = attr.ib(default=None)
    last_modified = attr.ib(metadata={READ_ONLY: True}, default=None)


@attr.s
class Change(OptimizelyDocument):
    dependencies = attr.ib()
    id = attr.ib()
    type = attr.ib()
    async = attr.ib(default=None)
    allow_additional_redirect = attr.ib(default=None)
    attributes = attr.ib(default=None)
    config = attr.ib(default=None)
    css = attr.ib(default=None)
    destination = attr.ib(default=None)
    destination_function = attr.ib(default=None)
    extension_id = attr.ib(default=None, metadata={READ_ONLY: True})
    name = attr.ib(default=None)
    operator = attr.ib(default=None)
    preserve_parameters = attr.ib(default=None)
    rearrange = attr.ib(default=None)
    selector = attr.ib(default=None)
    src = attr.ib(default=None, metadata={READ_ONLY: True})
    value = attr.ib(default=None, metadata={SERIALIZER: StaticContentSerializer})

    @property
    def dirname(self):
        return slugify(self.id)


@attr.s
class Action(OptimizelyDocument):
    changes = subdocuments(Change)
    page_id = attr.ib()
    share_link = attr.ib(default=None)

    @property
    def dirname(self):
        return slugify(str(self.page_id))


@attr.s
class Variation(OptimizelyDocument):
    weight = attr.ib()
    actions = subdocuments(Action)
    archived = attr.ib()
    variation_id = attr.ib()
    status = attr.ib(metadata={READ_ONLY: True})
    share_link = attr.ib(default=None)
    key = attr.ib(default=None)
    name = attr.ib(default=None)

    @property
    def dirname(self):
        return slugify("{} {}".format(self.name, self.variation_id))


@attr.s
class Experiment(OptimizelyDocument):
    changes = subdocuments(Change)
    created = attr.ib(metadata={READ_ONLY: True})
    id = attr.ib()
    is_classic = attr.ib(metadata={READ_ONLY: True})
    last_modified = attr.ib(metadata={READ_ONLY: True})
    metrics = attr.ib()
    project_id = attr.ib()
    status = attr.ib(metadata={READ_ONLY: True})
    type = attr.ib()
    variations = subdocuments(Variation)
    allocation_policy = attr.ib(default=None)
    page_ids = attr.ib(default=None)
    audience_conditions = attr.ib(default=None, metadata={SERIALIZER: ConditionSerializer})
    campaign_id = attr.ib(default=None)
    description = attr.ib(default=None)
    earliest = attr.ib(default=None, metadata={READ_ONLY: True})
    holdback = attr.ib(default=None)
    key = attr.ib(default=None)
    latest = attr.ib(default=None, metadata={READ_ONLY: True})
    name = attr.ib(default=None)
    schedule = attr.ib(default=None)
    url_targeting = attr.ib(default=None)


def write_meta_file(root, meta_document):
    meta_file = root / META_FILE

    with meta_file.open('w') as meta_file_handle:
        yaml.safe_dump(
            {k: v for k, v in meta_document.items() if v is not None},
            stream=meta_file_handle,
            default_flow_style=False,
        )


def read_meta_file(root):
    meta_file = root / META_FILE

    with meta_file.open() as meta_file_handle:
        return yaml.safe_load(meta_file_handle)


def slugify(directory):
    return directory.replace(' ', '_')


def as_non_null_dict(obj):
    return attr.asdict(obj, filter=lambda a, v: v is not None)


@click.group()
@click.password_option('--token', envvar='OPTIMIZELY_TOKEN')
@click.option('--verbose', default=False, is_flag=True)
@click.pass_context
def cli(ctx, token, verbose):
    log_level = logging.DEBUG if verbose else logging.INFO
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    logging.basicConfig(level=log_level, format=log_format)

    if ctx.obj is None:
        ctx.obj = {}

    ctx.obj['OPTIMIZELY'] = Optimizely(token)


@cli.command()
@click.option('--root', default='.', type=click.Path(exists=True, file_okay=False))
@click.pass_context
def pull(ctx, root):
    optimizely = ctx.obj['OPTIMIZELY']
    project_root = Path(root)
    for project in optimizely.projects().values():
        LOG.debug(f'Processing project: {project.name} ({project.id})')
        project.write_to_disk(project_root)

        for object_type in ('experiments', 'audiences', 'pages', 'events'):
            obj_root = project_root / project.dirname / object_type
            for obj in getattr(optimizely, object_type)(project.id).values():
                obj.write_to_disk(obj_root)


@cli.command('pull-experiment')
@click.argument('experiment', type=click.Path(exists=True, file_okay=False))
@click.pass_context
def pull_experiment(ctx, experiment):
    return pull_object(ctx, experiment, Experiment, 'experiments')


def pull_object(ctx, path, object_class, collection_name):
    optimizely = ctx.obj['OPTIMIZELY']

    local = object_class.read_from_disk(Path(path))
    remote = getattr(optimizely, collection_name)()[local.id]

    remote.write_to_disk(Path(path).parent)


@cli.command('push-experiment')
@click.argument('experiment', type=click.Path(exists=True, file_okay=False))
@click.option('--context-lines', '-n', type=int, default=3)
@click.pass_context
def push_experiment(ctx, experiment, context_lines):
    return push_object(ctx, experiment, Experiment, 'experiments', context_lines)


def push_object(ctx, path, object_class, collection_name, context_lines):
    optimizely = ctx.obj['OPTIMIZELY']

    local = object_class.read_from_disk(Path(path))
    remote = getattr(optimizely, collection_name)()[local.id]

    remote_doc = attr.asdict(remote, filter=modifiable)
    local_doc = attr.asdict(local, filter=modifiable)

    if local_doc == remote_doc:
        click.secho("No changes!", fg='green')
    else:
        for diffline in difflib.unified_diff(
            yaml.dump(remote_doc, default_flow_style=False).splitlines(),
            yaml.dump(local_doc, default_flow_style=False).splitlines(),
            fromfile='remote',
            tofile='local',
            n=context_lines,
        ):
            if diffline.startswith(' '):
                click.secho(diffline, fg='white')
            elif diffline.startswith('-'):
                click.secho(diffline, fg='red')
            elif diffline.startswith('+'):
                click.secho(diffline, fg='green')
            elif diffline.startswith('?'):
                click.secho(diffline, fg='yellow')

        if click.confirm('Push these changes?'):
            getattr(optimizely, collection_name)()[local.id] = local


@cli.command('pull-page')
@click.argument('page', type=click.Path(exists=True, file_okay=False))
@click.pass_context
def pull_page(ctx, page):
    return pull_object(ctx, page, Page, 'pages')


@cli.command('push-page')
@click.argument('page', type=click.Path(exists=True, file_okay=False))
@click.option('--context-lines', '-n', type=int, default=3)
@click.pass_context
def push_page(ctx, page, context_lines):
    return push_object(ctx, page, Page, 'pages', context_lines)


@cli.command('pull-project')
@click.argument('project', type=click.Path(exists=True, file_okay=False))
@click.pass_context
def pull_project(ctx, project):
    return pull_object(ctx, project, Project, 'projects')


@cli.command('push-project')
@click.argument('project', type=click.Path(exists=True, file_okay=False))
@click.option('--context-lines', '-n', type=int, default=3)
@click.pass_context
def push_page(ctx, project, context_lines):
    return push_object(ctx, project, Project, 'projects', context_lines)


if __name__ == '__main__':
    cli()
