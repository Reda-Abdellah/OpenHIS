window.config = {
  routerBasename: '/ohif/',
  showStudyList: true,
  extensions: [],
  modes: [],
  customizationService: {},
  defaultDataSourceName: 'dicomweb',
  dataSources: [
    {
      namespace: '@ohif/extension-default.dataSourcesModule.dicomweb',
      sourceName: 'dicomweb',
      configuration: {
        friendlyName: 'Orthanc DICOMweb',
        name: 'orthanc',
        wadoUriRoot:  '/orthanc/dicom-web',
        qidoRoot:     '/orthanc/dicom-web',
        wadoRoot:     '/orthanc/dicom-web',
        qidoSupportsIncludeField: false,
        imageRendering:     'wadors',
        thumbnailRendering: 'wadors',
        enableStudyLazyLoad: true,
        supportsFuzzyMatching: false,
        supportsWildcard: true,
        dicomUploadEnabled: true,
      },
    },
  ],
};
